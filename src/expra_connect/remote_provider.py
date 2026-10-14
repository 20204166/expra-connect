"""Client-side facade for authenticated operations on one remote node."""

from __future__ import annotations

import json
import threading
import time
from collections.abc import Callable
from typing import Any

from .identity import NodeId
from .models import NodePermission, ProcessActionKind
from .observability import ObservabilityWatcher, observation_scope
from .pairing.models import PairingTransaction
from .provider_requests import ProviderRequestMixin
from .remote_models import (
    ClusterDataError,
    NodeSnapshot,
    ProcessActionResult,
    ProcessCandidate,
    ProcessRef,
    ProcessTerminationRequest,
    ResourceSummary,
    file_candidate_from_dict,
    node_snapshot_from_dict,
    process_action_result_from_dict,
    process_candidate_from_dict,
    resource_summary_from_dict,
)
from .remote_role_operations import RemoteRoleOperations
from .socket_transport import _invoke_with_optional_cancel
from .surfaces.provider import SurfaceProviderMixin
from .wire_protocol import (
    DEFAULT_FRESHNESS_SECONDS,
    PAIRING_MODE_TRANSACTIONAL,
    RemoteAuthError,
    RemoteExecutionError,
    RemoteProtocolError,
    validate_hello_payload,
)

__all__ = ["AuthenticatedNodeProvider"]


class AuthenticatedNodeProvider(
    SurfaceProviderMixin, ProviderRequestMixin, RemoteRoleOperations
):
    """Client-side provider for one authenticated remote node."""

    def __init__(
        self,
        *,
        node_id: NodeId,
        secret: str,
        transport: Any,
        clock: Callable[[], float] = time.time,
        freshness_seconds: float = DEFAULT_FRESHNESS_SECONDS,
        caller_node_id: NodeId | None = None,
        observer: ObservabilityWatcher | None = None,
    ) -> None:
        self._node_id, self._caller_node_id = node_id, caller_node_id
        self._secret, self._transport, self._clock = secret, transport, clock
        self._freshness_seconds = freshness_seconds
        self._observer, self._invalidated = observer, False
        self._session_lock = threading.Lock()
        self._session_request_sequence = 0
        self._session_response_sequence = 0
        self._session_id: str | None = None

    def _request(
        self,
        op: str,
        params: dict[str, Any],
        cancel_event: Any | None = None,
    ) -> dict[str, Any]:
        with observation_scope(
            self._observer,
            f"remote:{op}",
            cancelled=cancel_event.is_set if cancel_event is not None else None,
        ):
            result: dict[str, Any] = super()._request(op, params, cancel_event)
        return result

    def invalidate(self) -> None:
        """Disable this provider after its local trust record is revoked."""
        self._invalidated = True

    def hello(self, cancel_event: Any | None = None) -> dict[str, Any]:
        payload = self._request("hello", {}, cancel_event)
        validate_hello_payload(payload, expected_node_id=self._node_id)
        return payload

    def request_shared(
        self,
        capability: str,
        params: dict[str, Any] | None = None,
        cancel_event: Any | None = None,
    ) -> Any:
        """Invoke a target-owned, explicitly granted shared capability."""

        payload = self._request(
            "capability_request",
            {"capability": capability, "params": params or {}},
            cancel_event,
        )
        if "result" not in payload:
            raise RemoteProtocolError("shared capability response has no result")
        return payload["result"]

    def revoke_self(self, cancel_event: Any | None = None) -> dict[str, Any]:
        """Ask the target to delete this caller's grant (self-revocation).

        The target authenticates the request with the current secret and then
        deletes the matching ``PeerGrantRecord``.  The same secret will fail on
        the very next request because the grant is gone from the target ACL.
        Idempotent: a missing grant is treated as already revoked (success).
        """
        return self._request("revoke_self", {}, cancel_event)

    @staticmethod
    def request_pairing(
        *,
        transport: Any,
        caller_node_id: NodeId,
        identity_fingerprint: str,
        transport_fingerprint: str,
        proposed_secret: str,
        root_public_key: str | None = None,
        transport_generation: int | None = None,
        transport_proof: str | None = None,
        permissions: frozenset[NodePermission],
        cancel_event: Any | None = None,
        intent: str = "pair",
    ) -> bool | dict[str, Any]:
        """Ask the target to approve and persist a pending pairing."""

        if cancel_event is not None and cancel_event.is_set():
            return False

        envelope = json.dumps(
            {
                "op": "pair_request",
                "pairing_mode": PAIRING_MODE_TRANSACTIONAL,
                "caller_node_id": caller_node_id.value,
                "identity_fingerprint": identity_fingerprint,
                "transport_fingerprint": transport_fingerprint,
                "root_public_key": root_public_key,
                "transport_generation": transport_generation,
                "transport_proof": transport_proof,
                "secret": proposed_secret,
                "permissions": sorted(permission.value for permission in permissions),
                "intent": intent,
            }
        )
        response_text = _invoke_with_optional_cancel(
            transport.request, envelope, cancel_event
        )
        if cancel_event is not None and cancel_event.is_set():
            return False
        response = json.loads(response_text)
        if not isinstance(response, dict) or response.get("approved") is not True:
            return False
        transaction_id = response.get("transaction_id")
        if not isinstance(transaction_id, str) or not transaction_id:
            return False
        return response

    @staticmethod
    def pairing_control(
        *,
        transport: Any,
        operation: str,
        transaction: PairingTransaction,
        cancel_event: Any | None = None,
    ) -> bool:
        if operation not in {"pair_confirm", "pair_abort"}:
            raise ValueError("invalid pairing control operation")
        if cancel_event is not None and cancel_event.is_set():
            return False
        envelope = json.dumps(
            {
                "op": operation,
                "transaction_id": transaction.transaction_id,
                "caller_node_id": transaction.caller_node_id,
                "identity_fingerprint": transaction.identity_fingerprint,
                "transport_fingerprint": transaction.transport_fingerprint,
                "secret": transaction.secret,
                "permissions": sorted(
                    permission.value for permission in transaction.permissions
                ),
                "expires_at": transaction.expires_at,
            }
        )
        response_text = _invoke_with_optional_cancel(
            transport.request, envelope, cancel_event
        )
        response = json.loads(response_text)
        return isinstance(response, dict) and response.get("approved") is True

    @staticmethod
    def confirm_pairing(
        transaction: PairingTransaction, cancel_event: Any | None = None
    ) -> bool:
        return AuthenticatedNodeProvider.pairing_control(
            transport=transaction.transport,
            operation="pair_confirm",
            transaction=transaction,
            cancel_event=cancel_event,
        )

    @staticmethod
    def abort_pairing(
        transaction: PairingTransaction, cancel_event: Any | None = None
    ) -> bool:
        return AuthenticatedNodeProvider.pairing_control(
            transport=transaction.transport,
            operation="pair_abort",
            transaction=transaction,
            cancel_event=cancel_event,
        )

    @staticmethod
    def request_elevation(
        *,
        transport: Any,
        caller_node_id: NodeId,
        identity_fingerprint: str,
        transport_fingerprint: str,
        current_secret: str,
        proposed_secret: str,
        permissions: frozenset[NodePermission],
    ) -> bool:
        """Request target approval for permissions beyond read-only pairing."""
        response = json.loads(
            transport.request(
                json.dumps(
                    {
                        "op": "elevation_request",
                        "caller_node_id": caller_node_id.value,
                        "identity_fingerprint": identity_fingerprint,
                        "transport_fingerprint": transport_fingerprint,
                        "current_secret": current_secret,
                        "secret": proposed_secret,
                        "permissions": sorted(
                            permission.value for permission in permissions
                        ),
                    }
                )
            )
        )
        return isinstance(response, dict) and response.get("approved") is True

    def dashboard_snapshot(
        self,
        cancel_event: Any | None = None,
        progress_callback: Callable[[str], None] | None = None,
    ) -> Any:
        snapshot = self._node_snapshot(cancel_event)
        if snapshot.dashboard is None:
            raise RemoteExecutionError("remote sent no dashboard data")
        return snapshot.dashboard

    def node_snapshot(
        self,
        cancel_event: Any | None = None,
        progress_callback: Callable[[str], None] | None = None,
    ) -> NodeSnapshot:
        return self._node_snapshot(cancel_event)

    def _node_snapshot(self, cancel_event: Any | None = None) -> NodeSnapshot:
        """Fetch and validate a snapshot before applying its public contract."""
        self._check_cancel(cancel_event)
        payload = self._request("dashboard_snapshot", {}, cancel_event)
        try:
            snapshot = node_snapshot_from_dict(payload["snapshot"])
        except (KeyError, ClusterDataError) as error:
            raise RemoteExecutionError("remote sent an invalid snapshot") from error
        if snapshot.node_id != self._node_id:
            raise RemoteAuthError("remote snapshot came from the wrong node")
        return snapshot

    def component_summary(
        self,
        key: str,
        cancel_event: Any | None = None,
    ) -> ResourceSummary:
        self._check_cancel(cancel_event)
        payload = self._request("component_summary", {"key": key}, cancel_event)
        if payload.get("node_id") != self._node_id.value:
            raise RemoteAuthError("remote resource came from the wrong node")
        try:
            return resource_summary_from_dict(payload["resource"])
        except ClusterDataError as error:
            raise RemoteExecutionError("remote sent an invalid resource") from error

    def process_candidates(
        self,
        cancel_event: Any | None = None,
    ) -> list[ProcessCandidate]:
        self._check_cancel(cancel_event)
        payload = self._request("process_candidates", {}, cancel_event)
        try:
            return [process_candidate_from_dict(item) for item in payload["processes"]]
        except (KeyError, TypeError, ClusterDataError) as error:
            raise RemoteExecutionError("remote sent invalid process data") from error

    def storage_candidates(
        self,
        progress_callback: Callable[[str], None] | None = None,
        cancel_event: Any | None = None,
    ) -> list[Any]:
        self._check_cancel(cancel_event)
        payload = self._request("storage_candidates", {}, cancel_event)
        try:
            return [file_candidate_from_dict(item) for item in payload["files"]]
        except (KeyError, TypeError, ClusterDataError) as error:
            raise RemoteExecutionError("remote sent invalid storage data") from error

    def request_quit(self, refs: list[dict[str, Any]]) -> ProcessActionResult:
        return self.terminate(self._typed_request(refs, ProcessActionKind.REQUEST_QUIT))

    def force_quit(self, refs: list[dict[str, Any]]) -> ProcessActionResult:
        return self.terminate(self._typed_request(refs, ProcessActionKind.FORCE_QUIT))

    def terminate(self, request: ProcessTerminationRequest) -> ProcessActionResult:
        if request.target_node_id != self._node_id:
            raise RemoteAuthError("process request target does not match provider")
        return self._process_action(
            "process_request_quit"
            if request.action is ProcessActionKind.REQUEST_QUIT
            else "process_force_quit",
            {
                "action": request.action.value,
                "processes": [
                    {"pid": ref.pid, "create_time": ref.create_time}
                    for ref in request.processes
                ],
            },
        )

    def _typed_request(
        self, refs: list[dict[str, Any]], action: ProcessActionKind
    ) -> ProcessTerminationRequest:
        return ProcessTerminationRequest(
            target_node_id=self._node_id,
            processes=tuple(
                ProcessRef(
                    node_id=self._node_id,
                    pid=ref["pid"],
                    create_time=ref["create_time"],
                )
                for ref in refs
            ),
            action=action,
        )

    def _process_action(
        self, operation: str, refs: dict[str, Any]
    ) -> ProcessActionResult:
        payload = self._request(operation, refs)
        try:
            return process_action_result_from_dict(payload)
        except (ClusterDataError, TypeError, ValueError) as error:
            raise RemoteExecutionError(
                "remote sent invalid process action data"
            ) from error

    def _role_request(self, operation: str, params: dict[str, Any]) -> dict[str, Any]:
        return self._request(operation, params)

    def reset_component_sample(self, key: str) -> None:
        return None

    def stop_background_workers(self) -> None:
        return None

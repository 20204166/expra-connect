"""Authenticated remote transport and remote-operation surface.

This module is the high-level remote API. It keeps the client-side
``AuthenticatedNodeProvider`` (a ``NodeProvider`` over an injected
``RemoteTransport``) and ``RemoteProcessActionBackend``, plus the server-side
``RemoteService`` (validates and solves via an injected provider). The
 lower-level remote-specific mechanics live in the protocol and transport modules:

- ``wire_protocol`` — the versioned HMAC request/response envelope, freshness
  window, bounded replay cache, and read-only operation/role metadata;
- ``socket_transport`` — the loopback ``MemoryRemoteTransport`` and the concrete
  ``SocketRemoteTransport``/``TLSRemoteTransport`` pair;
- ``server`` — the listening ``RemoteSocketServer``.

The transport is injected so tests and hosts never depend on sockets. Live
remote data features remain intentionally deferred elsewhere:
this module provides the secure contract and provider infrastructure, not the
feature wiring.
"""

import hmac
import inspect
import json
import logging
import math
import threading
import time
from collections.abc import Callable
from typing import Any

from .identity import NodeId, node_identity_fingerprint
from .models import (
    READ_CAPABILITIES,
    NodeCapability,
    NodePermission,
    ProcessActionKind,
)
from .pairing_models import PairingTransaction
from .process_backend import RemoteProcessActionBackend
from .provider_requests import ProviderRequestMixin
from .remote_models import (
    ClusterDataError,
    NodeSnapshot,
    NodeStatus,
    ProcessActionResult,
    ProcessCandidate,
    ProcessRef,
    ProcessTerminationRequest,
    ResourceSummary,
    file_candidate_from_dict,
    file_candidate_to_dict,
    node_snapshot_from_dict,
    node_snapshot_to_dict,
    process_action_result_from_dict,
    process_action_result_to_dict,
    process_candidate_from_dict,
    process_candidate_to_dict,
    resource_summary_from_dict,
    resource_summary_to_dict,
)
from .remote_role_operations import RemoteRoleOperations
from .server import (
    PEER_SERVICE_DEFAULT_PORT,
    RemoteSocketServer,
)
from .session import LogicalSessionRegistry
from .socket_transport import (
    MemoryRemoteTransport,
    SocketRemoteTransport,
    TLSRemoteTransport,
    build_trusted_transport,
)
from .wire_protocol import (
    DEFAULT_FRESHNESS_SECONDS,
    DEFAULT_IDEMPOTENCY_MAX_ENTRIES,
    DEFAULT_IDEMPOTENCY_TTL_SECONDS,
    DEFAULT_MAX_ACTIVE_HANDLERS,
    DEFAULT_REPLAY_MAX_ENTRIES,
    DEFAULT_REPLAY_TTL_SECONDS,
    MAX_ENVELOPE_BYTES,
    OP_REQUIRED_CAPABILITY,
    OP_REQUIRED_PERMISSION,
    OPERATION_SAFETY,
    PAIRING_MODE_TRANSACTIONAL,
    REMOTE_PROTOCOL_VERSION,
    ROLE_OPERATIONS,
    CapabilityElevationRequest,
    IdempotencyCache,
    PairingRequest,
    PeerGrant,
    RemoteAuthError,
    RemoteAuthorizationError,
    RemoteExecutionError,
    RemoteProtocolError,
    RemoteRequest,
    RemoteResponse,
    RemoteTransportError,
    RemoteUnavailableError,
    ReplayCache,
    parse_hello_capabilities,
    sign_request,
    sign_response,
    validate_hello_payload,
    validate_operation_params,
    verify_request,
    verify_response,
)

LOGGER = logging.getLogger(__name__)


__all__ = [
    "DEFAULT_FRESHNESS_SECONDS",
    "DEFAULT_IDEMPOTENCY_MAX_ENTRIES",
    "DEFAULT_IDEMPOTENCY_TTL_SECONDS",
    "DEFAULT_MAX_ACTIVE_HANDLERS",
    "DEFAULT_REPLAY_MAX_ENTRIES",
    "DEFAULT_REPLAY_TTL_SECONDS",
    "MAX_ENVELOPE_BYTES",
    "OPERATION_SAFETY",
    "OP_REQUIRED_CAPABILITY",
    "OP_REQUIRED_PERMISSION",
    "PAIRING_MODE_TRANSACTIONAL",
    "PEER_SERVICE_DEFAULT_PORT",
    "READ_CAPABILITIES",
    "REMOTE_PROTOCOL_VERSION",
    "ROLE_OPERATIONS",
    "AuthenticatedNodeProvider",
    "CapabilityElevationRequest",
    "MemoryRemoteTransport",
    "PairingRequest",
    "PairingTransaction",
    "PeerGrant",
    "RemoteAuthError",
    "RemoteAuthorizationError",
    "RemoteExecutionError",
    "RemoteProcessActionBackend",
    "RemoteProtocolError",
    "RemoteRequest",
    "RemoteResponse",
    "RemoteService",
    "RemoteSocketServer",
    "RemoteTransportError",
    "RemoteUnavailableError",
    "ReplayCache",
    "SocketRemoteTransport",
    "TLSRemoteTransport",
    "build_trusted_transport",
    "parse_hello_capabilities",
    "sign_request",
    "sign_response",
    "validate_hello_payload",
    "validate_operation_params",
    "verify_request",
    "verify_response",
]


class RemoteService:
    """Server-side boundary: verify, authorize, and solve one request.

    Serves one machine's read data through an injected ``NodeProvider``. Every
    response is signed with the peer's secret, so a client can always tell an
    authentic denial from a forgery. Auth failures raise (the transport closes
    silently); authorised-but-failed executions return a signed error envelope.
    """

    def __init__(
        self,
        *,
        node_id: NodeId,
        display_name: str,
        hostname: str,
        platform: str | None,
        status: NodeStatus,
        capabilities: frozenset[NodeCapability],
        provider: Any,
        secret: str,
        app_version: str = "",
        clock: Callable[[], float] = time.time,
        freshness_seconds: float = DEFAULT_FRESHNESS_SECONDS,
        replay_cache: ReplayCache | None = None,
        permissions: frozenset[NodePermission] | None = None,
        process_manager: Any | None = None,
        expected_caller_id: NodeId | None = None,
        grants: dict[NodeId, PeerGrant] | None = None,
        identity_fingerprint: str | None = None,
        transport_fingerprint: str | None = None,
        root_public_key: str | None = None,
        transport_generation: int | None = None,
        transport_proof: str | None = None,
        cluster_id: str | None = None,
        coordinator_epoch: int | None = None,
        fencing_token: str | None = None,
        role_handler: Callable[[RemoteRequest], dict[str, Any]] | None = None,
        trust_revoke_handler: Callable[[NodeId], dict[str, Any]] | None = None,
        require_dashboard_share: bool = False,
        capability_share: Any | None = None,
        idempotency_cache: IdempotencyCache | None = None,
        idempotency_ttl_seconds: float = DEFAULT_IDEMPOTENCY_TTL_SECONDS,
        idempotency_max_entries: int = DEFAULT_IDEMPOTENCY_MAX_ENTRIES,
        idempotency_store: Any | None = None,
    ) -> None:
        self._node_id = node_id
        self._display_name = display_name
        self._hostname = hostname
        self._platform = platform
        self._status = status
        self._capabilities = capabilities
        self._provider = provider
        self._process_manager = process_manager
        self._expected_caller_id = expected_caller_id
        self._identity_fingerprint = identity_fingerprint or node_identity_fingerprint(
            node_id
        )
        self._transport_fingerprint = transport_fingerprint
        self._root_public_key = root_public_key
        self._transport_generation = transport_generation
        self._transport_proof = transport_proof
        self._validate_secret(secret)
        self._secret = secret
        self._grant_mode = grants is not None
        self._grant_lock = threading.RLock()
        self._grants = dict(grants or {})
        for grant in self._grants.values():
            self._validate_secret(grant.secret)
            if grant.expires_at is not None and not math.isfinite(
                float(grant.expires_at)
            ):
                raise ValueError("peer grant expiry must be finite")
        self._app_version = app_version
        self._clock = clock
        self._freshness_seconds = freshness_seconds
        self._replay_cache = (
            replay_cache if replay_cache is not None else ReplayCache(clock=clock)
        )
        self._permissions = (
            frozenset(permissions)
            if permissions is not None
            else frozenset(
                permission
                for permission in NodePermission
                if permission.value in {capability.value for capability in capabilities}
                and permission
                not in {
                    NodePermission.PROCESS_TERMINATION,
                    NodePermission.PROCESS_FORCE_TERMINATION,
                    NodePermission.CLEANUP,
                }
            )
        )
        self._cluster_id = cluster_id
        self._coordinator_epoch = coordinator_epoch
        self._fencing_token = fencing_token
        self._role_handler = role_handler
        self._trust_revoke_handler = trust_revoke_handler
        self._require_dashboard_share = require_dashboard_share
        self._dashboard_shares: dict[NodeId, float] = {}
        self._capability_share = capability_share
        self._idempotency = idempotency_cache or IdempotencyCache(
            clock=clock,
            ttl_seconds=idempotency_ttl_seconds,
            max_entries=idempotency_max_entries,
            state_store=idempotency_store,
        )
        self._sessions = LogicalSessionRegistry(
            clock=clock, ttl_seconds=freshness_seconds * 10
        )
        self._grant_version = 0

    @staticmethod
    def _validate_secret(secret: str) -> None:
        if not isinstance(secret, str) or len(secret) != 64:
            raise ValueError("peer credentials must be 256-bit hex text")
        try:
            bytes.fromhex(secret)
        except ValueError as error:
            raise ValueError("peer credentials must be hexadecimal") from error

    def handle(
        self, envelope_text: str, *, connection_generation: str | None = None
    ) -> str:
        try:
            envelope = json.loads(envelope_text)
        except (ValueError, TypeError) as error:
            raise RemoteProtocolError("envelope is not valid JSON") from error
        caller_node_id = self._caller_from_json(envelope)
        credential = self._secret
        grant: PeerGrant | None = None
        with self._grant_lock:
            if self._grant_mode:
                if caller_node_id is None:
                    raise RemoteAuthError("request caller identity is required")
                grant = self._grants.get(caller_node_id)
                if grant is None:
                    raise RemoteAuthError("request caller identity is unknown")
                if grant.expires_at is not None and self._clock() >= grant.expires_at:
                    raise RemoteAuthError("request caller grant has expired")
                credential = grant.secret
        try:
            request = verify_request(
                envelope,
                secret=credential,
                clock=self._clock,
                freshness_seconds=self._freshness_seconds,
                replay_cache=self._replay_cache,
            )
        except RemoteAuthError as error:
            op = envelope.get("op") if isinstance(envelope, dict) else None
            operation_safety = OPERATION_SAFETY.get(op) if isinstance(op, str) else None
            if "replayed" not in str(error) or operation_safety not in {
                "retry_safe",
                "unsafe",
            }:
                raise
            request = verify_request(
                envelope,
                secret=credential,
                clock=self._clock,
                freshness_seconds=self._freshness_seconds,
                replay_cache=ReplayCache(clock=self._clock),
            )
        response_secret = grant.secret if grant is not None else self._secret
        if (
            self._expected_caller_id is not None
            and request.caller_node_id != self._expected_caller_id
        ):
            raise RemoteAuthError("request caller identity is invalid")
        if request.node_id != self._node_id:
            raise RemoteAuthError("request target identity is invalid")
        session_id = self._sessions.authenticate(
            request, connection_generation=connection_generation
        )
        if request.op in ROLE_OPERATIONS:
            self._verify_role_fence(request)
        grant_version = self._grant_version
        cache_key = (
            request.caller_node_id.value
            if request.caller_node_id is not None
            else request.node_id.value,
            request.request_id,
        )
        fingerprint = json.dumps(
            {"op": request.op, "params": request.params},
            sort_keys=True,
            separators=(",", ":"),
        )

        def solve() -> dict[str, Any]:
            result = self._solve(request, grant=grant)
            self._sessions.assert_current(session_id, connection_generation)
            with self._grant_lock:
                if grant_version != self._grant_version:
                    raise RemoteAuthorizationError("caller permission changed")
            return result

        try:
            payload = (
                self._idempotency.run(cache_key, fingerprint, solve)
                if OPERATION_SAFETY.get(request.op) != "read"
                else solve()
            )
        except RemoteAuthorizationError as error:
            response = sign_response(
                node_id=self._node_id.value,
                request_id=request.request_id,
                status="error",
                error=(
                    "capability_unavailable"
                    if "not authorised" in str(error)
                    else "permission_denied"
                ),
                timestamp=self._clock(),
                secret=response_secret,
                session_id=session_id,
                connection_generation=connection_generation,
            )
            return json.dumps(response)
        except RemoteUnavailableError:
            response = sign_response(
                node_id=self._node_id.value,
                request_id=request.request_id,
                status="error",
                error="target_offline",
                timestamp=self._clock(),
                secret=response_secret,
                session_id=session_id,
                connection_generation=connection_generation,
            )
            return json.dumps(response)
        except RemoteProtocolError:
            raise
        except Exception:  # noqa: BLE001 - failures become stable signed errors.
            LOGGER.warning(
                "Remote operation %s failed on %s",
                request.op,
                self._node_id,
            )
            response = sign_response(
                node_id=self._node_id.value,
                request_id=request.request_id,
                status="error",
                error="execution_failed",
                timestamp=self._clock(),
                secret=response_secret,
                session_id=session_id,
                connection_generation=connection_generation,
            )
            return json.dumps(response)
        response = sign_response(
            node_id=self._node_id.value,
            request_id=request.request_id,
            status="ok",
            payload=payload,
            timestamp=self._clock(),
            secret=response_secret,
            session_id=session_id,
            connection_generation=connection_generation,
        )
        return json.dumps(response)

    def update_grants(self, grants: dict[NodeId, PeerGrant]) -> None:
        """Replace the live target ACL after an atomic settings update."""
        validated = dict(grants)
        for grant in validated.values():
            self._validate_secret(grant.secret)
            if grant.expires_at is not None and not math.isfinite(
                float(grant.expires_at)
            ):
                raise ValueError("peer grant expiry must be finite")
        with self._grant_lock:
            self._grant_mode = True
            self._grants = validated
            self._grant_version += 1

    @staticmethod
    def _caller_from_json(envelope: Any) -> NodeId | None:
        if not isinstance(envelope, dict):
            return None
        caller = envelope.get("caller_node_id")
        return NodeId(caller) if isinstance(caller, str) and caller else None

    def _solve(
        self, request: RemoteRequest, *, grant: PeerGrant | None = None
    ) -> dict[str, Any]:
        required = OP_REQUIRED_CAPABILITY.get(request.op)
        if required is None:
            raise RemoteProtocolError(f"unknown operation: {request.op}")
        if required not in self._capabilities:
            raise RemoteAuthorizationError(f"node is not authorised for {request.op}")
        permission = OP_REQUIRED_PERMISSION[request.op]
        permissions = grant.permissions if grant is not None else self._permissions
        if permission not in permissions:
            raise RemoteAuthorizationError(f"caller lacks permission for {request.op}")
        validate_operation_params(request.op, request.params)
        if request.op in ROLE_OPERATIONS:
            if self._role_handler is None:
                raise RemoteUnavailableError("role operations are unavailable")
            return self._role_handler(request)
        if request.op == "revoke_self":
            if request.caller_node_id is None:
                raise RemoteAuthorizationError(
                    "caller identity required for self-revocation"
                )
            if self._trust_revoke_handler is None:
                raise RemoteUnavailableError("trust revocation is unavailable")
            return self._trust_revoke_handler(request.caller_node_id)
        if request.op == "capability_request":
            if request.caller_node_id is None:
                raise RemoteAuthorizationError(
                    "caller identity required for capability requests"
                )
            if self._capability_share is None:
                raise RemoteUnavailableError("capability sharing is unavailable")
            try:
                result = self._capability_share.request(
                    request.caller_node_id,
                    request.params["capability"],
                    request.params["params"],
                )
            except PermissionError as error:
                raise RemoteAuthorizationError(str(error)) from error
            return {"result": result}
        if request.op == "hello":
            return {
                "ok": True,
                "node_id": self._node_id.value,
                "identity_fingerprint": self._identity_fingerprint,
                "transport_fingerprint": self._transport_fingerprint,
                "root_public_key": self._root_public_key,
                "transport_generation": self._transport_generation,
                "transport_proof": self._transport_proof,
                "protocol_version": REMOTE_PROTOCOL_VERSION,
                "app_version": self._app_version,
                "capabilities": sorted(
                    capability.value for capability in self._capabilities
                ),
            }
        if request.op == "dashboard_snapshot":
            if (
                self._require_dashboard_share
                and request.caller_node_id not in self._dashboard_shares
            ):
                raise RemoteAuthorizationError("dashboard share is not active")
            if request.caller_node_id is not None:
                expiry = self._dashboard_shares.get(request.caller_node_id)
                if expiry is not None and self._clock() >= expiry:
                    self._dashboard_shares.pop(request.caller_node_id, None)
                    raise RemoteAuthorizationError("dashboard share has expired")
            snapshot = self._dashboard_snapshot()
            return {"snapshot": node_snapshot_to_dict(snapshot)}
        if request.op == "component_summary":
            resource = self._provider.component_summary(request.params["key"])
            return {
                "node_id": self._node_id.value,
                "resource": resource_summary_to_dict(resource),
            }
        if request.op == "process_candidates":
            processes = self._provider.process_candidates()
            return {"processes": [process_candidate_to_dict(p) for p in processes]}
        if request.op in {"process_request_quit", "process_force_quit"}:
            if self._status is not NodeStatus.ONLINE:
                raise RemoteUnavailableError("target is offline")
            if self._process_manager is None:
                raise RemoteExecutionError("process actions are unavailable")
            refs = request.params["processes"]
            action_kind = ProcessActionKind(request.params["action"])
            termination = ProcessTerminationRequest(
                target_node_id=self._node_id,
                processes=tuple(
                    ProcessRef(
                        node_id=self._node_id,
                        pid=int(item["pid"]),
                        create_time=float(item["create_time"]),
                    )
                    for item in refs
                ),
                action=action_kind,
            )
            if hasattr(self._process_manager, "terminate"):
                result = self._process_manager.terminate(termination)
            else:
                pids = [process.pid for process in termination.processes]
                create_times = {
                    process.pid: process.create_time
                    for process in termination.processes
                    if process.create_time is not None
                }
                action = (
                    self._process_manager.force_quit
                    if action_kind is ProcessActionKind.FORCE_QUIT
                    else self._process_manager.request_quit
                )
                result = action(pids, create_times)
            if not isinstance(result, ProcessActionResult):
                raise RemoteExecutionError("target returned an invalid action result")
            return process_action_result_to_dict(result)
        if request.op == "storage_candidates":
            candidates = self._provider.storage_candidates()
            return {"files": [file_candidate_to_dict(c) for c in candidates]}
        raise RemoteProtocolError(f"unknown operation: {request.op}")

    def _verify_role_fence(self, request: RemoteRequest) -> None:
        params = request.params
        if (
            self._cluster_id is not None
            and params.get("cluster_id") != self._cluster_id
        ):
            raise RemoteAuthorizationError("cluster identity is invalid")
        if (
            self._coordinator_epoch is not None
            and params.get("epoch") != self._coordinator_epoch
        ):
            raise RemoteAuthorizationError("coordinator epoch is stale")
        if self._fencing_token is not None and not hmac.compare_digest(
            str(params.get("fencing_token")), self._fencing_token
        ):
            raise RemoteAuthorizationError("coordinator fencing token is stale")

    def update_cluster_fence(
        self, *, cluster_id: str, coordinator_epoch: int, fencing_token: str
    ) -> None:
        self._cluster_id = cluster_id
        self._coordinator_epoch = coordinator_epoch
        self._fencing_token = fencing_token

    def clear_dashboard_share(self, caller_node_id: NodeId) -> None:
        self._dashboard_shares.pop(caller_node_id, None)

    def start_dashboard_share_for(
        self, caller_node_id: NodeId, *, expires_at: float
    ) -> None:
        if expires_at <= self._clock():
            raise RemoteAuthorizationError("dashboard share expiry is invalid")
        self._dashboard_shares[caller_node_id] = expires_at

    def stop_dashboard_shares(self) -> None:
        self._dashboard_shares.clear()

    def _dashboard_snapshot(self) -> NodeSnapshot:
        dashboard = self._provider.dashboard_snapshot()
        return NodeSnapshot(
            node_id=self._node_id,
            display_name=self._display_name,
            hostname=self._hostname,
            platform=self._platform,
            status=self._status,
            capabilities=self._capabilities,
            scanned_at=dashboard.scanned_at,
            dashboard=dashboard,
        )


class AuthenticatedNodeProvider(ProviderRequestMixin, RemoteRoleOperations):
    """Client-side ``NodeProvider`` over one authenticated remote node.

    Builds and signs every request, verifies every response, enforces
    freshness and request-id correlation, and maps transport/auth failures to
    clear exceptions. ``reset_component_sample``/``stop_background_workers``
    are local-only concerns and are read-only no-ops here.
    """

    def __init__(
        self,
        *,
        node_id: NodeId,
        secret: str,
        transport: Any,
        clock: Callable[[], float] = time.time,
        freshness_seconds: float = DEFAULT_FRESHNESS_SECONDS,
        caller_node_id: NodeId | None = None,
    ) -> None:
        self._node_id = node_id
        self._caller_node_id = caller_node_id
        self._secret = secret
        self._transport = transport
        self._clock = clock
        self._freshness_seconds = freshness_seconds
        self._invalidated = False
        self._session_id: str | None = None

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
            }
        )
        request = transport.request
        try:
            inspect.signature(request).bind(envelope, cancel_event)
        except (TypeError, ValueError):
            response_text = request(envelope)
        else:
            response_text = request(envelope, cancel_event)
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
        request = transport.request
        try:
            inspect.signature(request).bind(envelope, cancel_event)
        except (TypeError, ValueError):
            response_text = request(envelope)
        else:
            response_text = request(envelope, cancel_event)
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

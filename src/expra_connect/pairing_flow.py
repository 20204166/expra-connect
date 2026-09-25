"""Network pairing flow composed from the canonical pairing and transport owners."""

from __future__ import annotations

import logging
import secrets
import threading
from collections.abc import Callable, Mapping
from typing import Any

from .discovery import endpoint_rank
from .identity import NodeId, NodeIdentity, node_identity_fingerprint
from .models import (
    READ_PERMISSIONS,
    DiscoveredNodeCandidate,
    EndpointCandidate,
    NodePermission,
)
from .pairing import (
    IdentityConflict,
    PairingBusy,
    PairingConflict,
    PairingManager,
    PeerGrant,
    RelationshipState,
    RepairNotRequired,
    RepairRequired,
    TrustedPeer,
)
from .remote_service import AuthenticatedNodeProvider, PairingTransaction
from .socket_transport import TLSRemoteTransport
from .wire_protocol import RemoteTransportError

LOGGER = logging.getLogger(__name__)


class NetworkPairing:
    """Run request, target confirmation, local trust, and durable commit."""

    def __init__(
        self,
        *,
        identity: NodeIdentity,
        transport_fingerprint: str,
        root_public_key: str | None = None,
        transport_generation: int = 1,
        transport_proof: str | None = None,
        pairing: PairingManager,
        candidates: Mapping[str, DiscoveredNodeCandidate],
        persist: Callable[[], bool],
        on_route_attempt: Callable[[str, EndpointCandidate, str, str | None], None]
        | None = None,
    ) -> None:
        self._identity = identity
        self._transport_fingerprint = transport_fingerprint
        self._root_public_key = root_public_key
        self._transport_generation = transport_generation
        self._transport_proof = transport_proof
        self._pairing = pairing
        self._candidates = candidates
        self._persist = persist
        self._on_route_attempt = on_route_attempt

    def pair(
        self,
        peer_id: NodeId,
        *,
        permissions: frozenset[str],
        cancel_event: threading.Event | None = None,
    ) -> TrustedPeer:
        """Establish a missing outbound relationship, never replacing trust."""
        return self._establish(
            peer_id,
            permissions=permissions,
            cancel_event=cancel_event,
            intent="pair",
        )

    def repair(
        self,
        peer_id: NodeId,
        *,
        permissions: frozenset[str],
        cancel_event: threading.Event | None = None,
    ) -> TrustedPeer:
        """Replace broken directional credentials for a proven identity."""
        return self._establish(
            peer_id,
            permissions=permissions,
            cancel_event=cancel_event,
            intent="repair",
        )

    def _establish(
        self,
        peer_id: NodeId,
        *,
        permissions: frozenset[str],
        cancel_event: threading.Event | None,
        intent: str,
    ) -> TrustedPeer:
        if cancel_event is not None and cancel_event.is_set():
            raise RuntimeError("pairing cancelled")
        candidate = self._candidates.get(peer_id.value)
        if (
            candidate is None
            or not candidate.compatible
            or candidate.port is None
            or not candidate.addresses
        ):
            raise ConnectionError("peer must be discovered and connectable")
        if not candidate.transport_fingerprint:
            raise ValueError("peer advertisement has no transport fingerprint")
        if not permissions <= {permission.value for permission in READ_PERMISSIONS}:
            raise ValueError("pairing permissions must be read-only")
        previous = self._pairing.trusted.get(peer_id)
        previous_broken = self._pairing.is_auth_broken(peer_id)
        state = self._classify_candidate(peer_id, candidate)
        if intent == "pair":
            if state is RelationshipState.IDENTITY_CONFLICT:
                raise IdentityConflict(
                    "peer identity conflicts with stored trust",
                    peer_id=peer_id,
                    state=state,
                )
            if state in {
                RelationshipState.OUTBOUND_TRUSTED,
                RelationshipState.BIDIRECTIONAL,
            }:
                assert previous is not None
                return previous
            if state is RelationshipState.REPAIR_REQUIRED:
                raise RepairRequired(
                    "peer credentials require explicit repair",
                    peer_id=peer_id,
                    state=state,
                )
            if state is RelationshipState.PENDING_OUTBOUND:
                raise PairingBusy(
                    "a pairing transaction is already active for this peer",
                    peer_id=peer_id,
                    state=state,
                )
        else:
            if previous is None:
                raise PairingConflict(
                    "peer has no outbound relationship to repair",
                    peer_id=peer_id,
                    state=state,
                )
            if state is RelationshipState.IDENTITY_CONFLICT:
                raise IdentityConflict(
                    "peer cannot prove continuity of the stored root",
                    peer_id=peer_id,
                    state=state,
                )
            if state in {
                RelationshipState.OUTBOUND_TRUSTED,
                RelationshipState.BIDIRECTIONAL,
            }:
                raise RepairNotRequired(
                    "peer relationship does not require repair",
                    peer_id=peer_id,
                    state=state,
                )
            if state is not RelationshipState.REPAIR_REQUIRED:
                raise PairingConflict(
                    "peer relationship cannot be repaired",
                    peer_id=peer_id,
                    state=state,
                )
        pending = self._pairing.begin(
            peer_id,
            secret=secrets.token_hex(32),
            identity_fingerprint=node_identity_fingerprint(self._identity.node_id),
            transport_fingerprint=self._transport_fingerprint,
            root_public_key=self._root_public_key or self._identity.root_public_key,
            transport_generation=self._transport_generation,
            transport_proof=self._transport_proof
            or self._identity.sign_transport_proof(
                self._transport_generation, self._transport_fingerprint
            ),
            direction="outbound",
            intent=intent,
        )
        errors: list[BaseException] = []
        transport: Any | None = None
        response: bool | dict[str, Any] | None = None
        last_endpoint: EndpointCandidate | None = None
        for endpoint in sorted(candidate.endpoint_candidates, key=endpoint_rank):
            last_endpoint = endpoint
            self._report_route_attempt("pairing", endpoint, "started", None)
            try:
                transport = TLSRemoteTransport(
                    endpoint.address,
                    endpoint.port,
                    expected_fingerprint=candidate.transport_fingerprint,
                )
                response = AuthenticatedNodeProvider.request_pairing(
                    transport=transport,
                    caller_node_id=self._identity.node_id,
                    identity_fingerprint=node_identity_fingerprint(
                        self._identity.node_id
                    ),
                    transport_fingerprint=self._transport_fingerprint,
                    proposed_secret=pending.secret,
                    permissions=frozenset(
                        NodePermission(permission) for permission in permissions
                    ),
                    cancel_event=cancel_event,
                    intent=intent,
                )
                break
            except (OSError, ConnectionError, RemoteTransportError) as error:
                errors.append(error)
                self._report_route_attempt("pairing", endpoint, "failed", str(error))

        if response is None:
            self._rollback(peer_id, previous, previous_broken, pending.transaction_id)
            raise errors[-1] if errors else ConnectionError("pairing routes failed")
        if not isinstance(response, dict):
            if last_endpoint is not None:
                self._report_route_attempt(
                    "pairing", last_endpoint, "rejected", "peer rejected pairing"
                )
            self._rollback(peer_id, previous, previous_broken, pending.transaction_id)
            if cancel_event is not None and cancel_event.is_set():
                raise RuntimeError("pairing cancelled")
            raise PermissionError("peer rejected pairing")
        try:
            accepted_permissions = frozenset(
                NodePermission(item) for item in response["permissions"]
            )
        except (KeyError, TypeError, ValueError) as error:
            self._rollback(peer_id, previous, previous_broken, pending.transaction_id)
            raise ValueError("pairing response permissions are malformed") from error
        if (
            not accepted_permissions
            or not accepted_permissions <= frozenset(NodePermission)
            or not accepted_permissions
            <= frozenset(NodePermission(item) for item in permissions)
        ):
            self._rollback(peer_id, previous, previous_broken, pending.transaction_id)
            raise PermissionError("peer returned permissions outside the request")
        if last_endpoint is not None:
            self._report_route_attempt("pairing", last_endpoint, "succeeded", None)
        transaction = PairingTransaction(
            transaction_id=response["transaction_id"],
            caller_node_id=response["caller_node_id"],
            identity_fingerprint=response["identity_fingerprint"],
            transport_fingerprint=response["transport_fingerprint"],
            root_public_key=response.get("root_public_key"),
            transport_generation=response.get("transport_generation"),
            transport_proof=response.get("transport_proof"),
            secret=response["secret"],
            permissions=accepted_permissions,
            expires_at=response["expires_at"],
            transport=transport,
        )
        self._pairing.accept_grant(
            pending.transaction_id,
            peer_id,
            PeerGrant(
                self._identity.node_id,
                pending.secret,
                accepted_permissions,
                node_identity_fingerprint(self._identity.node_id),
                self._transport_fingerprint,
                self._root_public_key or self._identity.root_public_key,
                self._transport_generation,
                self._transport_proof
                or self._identity.sign_transport_proof(
                    self._transport_generation, self._transport_fingerprint
                ),
            ),
        )
        trusted = TrustedPeer(
            peer_id,
            pending.secret,
            accepted_permissions,
            candidate.identity_fingerprint,
            candidate.transport_fingerprint,
            candidate.root_public_key,
            candidate.transport_generation,
            candidate.transport_proof,
        )
        self._pairing.trusted[peer_id] = trusted
        if cancel_event is not None and cancel_event.is_set():
            self._rollback(peer_id, previous, previous_broken, pending.transaction_id)
            AuthenticatedNodeProvider.abort_pairing(transaction)
            raise RuntimeError("pairing cancelled")
        if not self._persist():
            try:
                AuthenticatedNodeProvider.abort_pairing(transaction)
            finally:
                self._rollback(
                    peer_id, previous, previous_broken, pending.transaction_id
                )
            raise RuntimeError("pairing was not durably persisted")
        if cancel_event is not None and cancel_event.is_set():
            self._rollback(peer_id, previous, previous_broken, pending.transaction_id)
            AuthenticatedNodeProvider.abort_pairing(transaction)
            raise RuntimeError("pairing cancelled")
        try:
            confirmed = AuthenticatedNodeProvider.confirm_pairing(
                transaction, cancel_event=cancel_event
            )
        except Exception:
            try:
                AuthenticatedNodeProvider.abort_pairing(transaction)
            finally:
                self._rollback(
                    peer_id, previous, previous_broken, pending.transaction_id
                )
            raise
        if not confirmed:
            try:
                AuthenticatedNodeProvider.abort_pairing(transaction)
            finally:
                self._rollback(
                    peer_id, previous, previous_broken, pending.transaction_id
                )
            raise PermissionError("peer did not confirm pairing")
        return trusted

    def _classify_candidate(
        self, peer_id: NodeId, candidate: DiscoveredNodeCandidate
    ) -> RelationshipState:
        return self._pairing.classify_trusted_candidate(
            peer_id,
            candidate_root_public_key=candidate.root_public_key,
            candidate_transport_fingerprint=candidate.transport_fingerprint,
            candidate_transport_generation=candidate.transport_generation,
            candidate_transport_proof=candidate.transport_proof,
        )

    def _rollback(
        self,
        peer_id: NodeId,
        previous: TrustedPeer | None,
        previous_broken: bool,
        transaction_id: str,
    ) -> None:
        self._pairing.abort(transaction_id)
        if previous is None:
            self._pairing.revoke_trusted(peer_id)
        else:
            self._pairing.trusted[peer_id] = previous
            if previous_broken:
                self._pairing.mark_auth_failure(peer_id)
            else:
                self._pairing.mark_auth_ok(peer_id)
        self._persist()

    def _abort(self, transaction_id: str) -> None:
        self._pairing.abort(transaction_id)
        self._persist()

    def _report_route_attempt(
        self,
        phase: str,
        endpoint: EndpointCandidate,
        outcome: str,
        error: str | None,
    ) -> None:
        if self._on_route_attempt is None:
            return
        try:
            self._on_route_attempt(phase, endpoint, outcome, error)
        except Exception:
            LOGGER.debug("Pairing route callback failed", exc_info=True)

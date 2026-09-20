"""Network pairing flow composed from the canonical pairing and transport owners."""

from __future__ import annotations

import secrets
import threading
from collections.abc import Callable, Mapping
from typing import Any

from .discovery import endpoint_rank
from .identity import NodeId, NodeIdentity, node_identity_fingerprint
from .models import READ_PERMISSIONS, DiscoveredNodeCandidate, NodePermission
from .pairing import PairingManager, PeerGrant, TrustedPeer
from .remote_service import AuthenticatedNodeProvider, PairingTransaction
from .socket_transport import TLSRemoteTransport
from .wire_protocol import RemoteTransportError


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
    ) -> None:
        self._identity = identity
        self._transport_fingerprint = transport_fingerprint
        self._root_public_key = root_public_key
        self._transport_generation = transport_generation
        self._transport_proof = transport_proof
        self._pairing = pairing
        self._candidates = candidates
        self._persist = persist

    def pair(
        self,
        peer_id: NodeId,
        *,
        permissions: frozenset[str],
        cancel_event: threading.Event | None = None,
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
        )
        errors: list[BaseException] = []
        transport: Any | None = None
        response: bool | dict[str, Any] | None = None
        for endpoint in sorted(candidate.endpoint_candidates, key=endpoint_rank):
            try:
                transport = TLSRemoteTransport(
                    endpoint.address,
                    endpoint.port,
                    expected_fingerprint=candidate.transport_fingerprint,
                )
                response = AuthenticatedNodeProvider.request_pairing(
                    transport=transport,
                    caller_node_id=self._identity.node_id,
                    identity_fingerprint=node_identity_fingerprint(self._identity.node_id),
                    transport_fingerprint=self._transport_fingerprint,
                    proposed_secret=pending.secret,
                    permissions=frozenset(
                        NodePermission(permission) for permission in permissions
                    ),
                    cancel_event=cancel_event,
                )
                break
            except (OSError, ConnectionError, RemoteTransportError) as error:
                errors.append(error)
        if response is None:
            self._abort(pending.transaction_id)
            raise errors[-1] if errors else ConnectionError("pairing routes failed")
        if not isinstance(response, dict):
            self._abort(pending.transaction_id)
            if cancel_event is not None and cancel_event.is_set():
                raise RuntimeError("pairing cancelled")
            raise PermissionError("peer rejected pairing")
        transaction = PairingTransaction(
            transaction_id=response["transaction_id"],
            caller_node_id=response["caller_node_id"],
            identity_fingerprint=response["identity_fingerprint"],
            transport_fingerprint=response["transport_fingerprint"],
            root_public_key=response.get("root_public_key"),
            transport_generation=response.get("transport_generation"),
            transport_proof=response.get("transport_proof"),
            secret=response["secret"],
            permissions=frozenset(
                NodePermission(item) for item in response["permissions"]
            ),
            expires_at=response["expires_at"],
            transport=transport,
        )
        self._pairing.accept_grant(
            pending.transaction_id,
            peer_id,
            PeerGrant(
                self._identity.node_id,
                pending.secret,
                permissions,
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
            permissions,
            candidate.identity_fingerprint,
            candidate.transport_fingerprint,
            candidate.root_public_key,
            candidate.transport_generation,
            candidate.transport_proof,
        )
        self._pairing.trusted[peer_id] = trusted
        if cancel_event is not None and cancel_event.is_set():
            self._pairing.revoke(peer_id)
            self._persist()
            AuthenticatedNodeProvider.abort_pairing(transaction)
            raise RuntimeError("pairing cancelled")
        if not self._persist():
            try:
                AuthenticatedNodeProvider.abort_pairing(transaction)
            finally:
                self._pairing.revoke(peer_id)
            raise RuntimeError("pairing was not durably persisted")
        try:
            confirmed = AuthenticatedNodeProvider.confirm_pairing(
                transaction, cancel_event=cancel_event
            )
        except Exception:
            try:
                AuthenticatedNodeProvider.abort_pairing(transaction)
            finally:
                self._pairing.revoke(peer_id)
                self._persist()
            raise
        if not confirmed:
            try:
                AuthenticatedNodeProvider.abort_pairing(transaction)
            finally:
                self._pairing.revoke(peer_id)
                self._persist()
            raise PermissionError("peer did not confirm pairing")
        return trusted

    def _abort(self, transaction_id: str) -> None:
        self._pairing.abort(transaction_id)
        self._persist()

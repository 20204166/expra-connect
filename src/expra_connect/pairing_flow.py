"""Network pairing flow composed from the canonical pairing and transport owners."""

from __future__ import annotations

import secrets
from collections.abc import Callable, Mapping

from .identity import NodeId, NodeIdentity, node_identity_fingerprint
from .models import READ_PERMISSIONS, DiscoveredNodeCandidate, NodePermission
from .pairing import PairingManager, PeerGrant, TrustedPeer
from .remote_service import AuthenticatedNodeProvider, PairingTransaction
from .socket_transport import TLSRemoteTransport


class NetworkPairing:
    """Run request, target confirmation, local trust, and durable commit."""

    def __init__(
        self,
        *,
        identity: NodeIdentity,
        transport_fingerprint: str,
        pairing: PairingManager,
        candidates: Mapping[str, DiscoveredNodeCandidate],
        persist: Callable[[], bool],
    ) -> None:
        self._identity = identity
        self._transport_fingerprint = transport_fingerprint
        self._pairing = pairing
        self._candidates = candidates
        self._persist = persist

    def pair(
        self,
        peer_id: NodeId,
        *,
        permissions: frozenset[str],
    ) -> TrustedPeer:
        candidate = self._candidates.get(peer_id.value)
        if candidate is None or candidate.port is None or not candidate.addresses:
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
        )
        transport = TLSRemoteTransport(
            candidate.addresses[0],
            candidate.port,
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
        )
        if not isinstance(response, dict):
            self._abort(pending.transaction_id)
            raise PermissionError("peer rejected pairing")
        transaction = PairingTransaction(
            transaction_id=response["transaction_id"],
            caller_node_id=response["caller_node_id"],
            identity_fingerprint=response["identity_fingerprint"],
            transport_fingerprint=response["transport_fingerprint"],
            secret=response["secret"],
            permissions=frozenset(
                NodePermission(item) for item in response["permissions"]
            ),
            expires_at=response["expires_at"],
            transport=transport,
        )
        if not AuthenticatedNodeProvider.confirm_pairing(transaction):
            self._abort(pending.transaction_id)
            raise PermissionError("peer did not confirm pairing")
        self._pairing.accept_grant(
            pending.transaction_id,
            peer_id,
            PeerGrant(
                self._identity.node_id,
                pending.secret,
                permissions,
                node_identity_fingerprint(self._identity.node_id),
                self._transport_fingerprint,
            ),
        )
        trusted = TrustedPeer(
            peer_id,
            pending.secret,
            permissions,
            candidate.identity_fingerprint,
            candidate.transport_fingerprint,
        )
        self._pairing.trusted[peer_id] = trusted
        if not self._persist():
            self._pairing.revoke(peer_id)
            raise RuntimeError("pairing was not durably persisted")
        return trusted

    def _abort(self, transaction_id: str) -> None:
        self._pairing.abort(transaction_id)
        self._persist()

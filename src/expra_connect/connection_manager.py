"""Outgoing connection lifecycle composed from trust, discovery, and transport owners."""

from __future__ import annotations

import time
from collections.abc import MutableMapping

from .connection_state import ConnectionState, ConnectionStatus, classify_peer_failure
from .identity import NodeId
from .models import DiscoveredNodeCandidate
from .pairing import PairingManager
from .registry import NodeRegistry
from .remote_service import AuthenticatedNodeProvider
from .socket_transport import TLSRemoteTransport
from .wire_protocol import (
    RemoteAuthError,
    RemoteExecutionError,
    RemoteTransportError,
    parse_hello_capabilities,
)


class ConnectionManager:
    """Own outgoing providers and project connection state into ``NodeRegistry``."""

    def __init__(
        self,
        *,
        local_id: NodeId,
        pairing: PairingManager,
        registry: NodeRegistry,
        candidates: MutableMapping[str, DiscoveredNodeCandidate],
    ) -> None:
        self._local_id = local_id
        self._pairing = pairing
        self._registry = registry
        self._candidates = candidates
        self._providers: dict[NodeId, AuthenticatedNodeProvider] = {}

    @property
    def providers(self) -> tuple[NodeId, ...]:
        return tuple(self._providers)

    def connect(self, peer_id: NodeId) -> AuthenticatedNodeProvider:
        trusted = self._pairing.trusted.get(peer_id)
        candidate = self._candidates.get(peer_id.value)
        if trusted is None or candidate is None or not candidate.compatible:
            raise PermissionError("peer must be discovered and trusted first")
        if candidate.port is None or not candidate.addresses:
            raise ConnectionError("peer has no connectable endpoint")
        if (
            trusted.transport_fingerprint is not None
            and candidate.transport_fingerprint != trusted.transport_fingerprint
        ):
            self._set_state(
                peer_id,
                ConnectionState(
                    ConnectionStatus.IDENTITY_CHANGED,
                    reason="peer transport fingerprint changed",
                    changed_at=time.time(),
                ),
            )
            raise RemoteAuthError("peer transport fingerprint changed")
        if not candidate.transport_fingerprint:
            raise RemoteAuthError("peer has no transport fingerprint")
        self._registry.observe(peer_id, frozenset())
        self._set_state(peer_id, ConnectionState.connecting(now=time.time()))
        provider = AuthenticatedNodeProvider(
            node_id=peer_id,
            caller_node_id=self._local_id,
            secret=trusted.secret,
            transport=TLSRemoteTransport(
                candidate.addresses[0],
                candidate.port,
                expected_fingerprint=candidate.transport_fingerprint,
            ),
        )
        try:
            capabilities = parse_hello_capabilities(provider.hello())
        except (
            OSError,
            ConnectionError,
            RemoteExecutionError,
            RemoteTransportError,
            RemoteAuthError,
        ) as error:
            failure = classify_peer_failure(error)
            status = (
                ConnectionStatus.AUTHENTICATION_FAILED
                if failure.value in {"authentication_failed", "identity_changed"}
                else ConnectionStatus.OFFLINE
            )
            self._set_state(
                peer_id,
                ConnectionState(status, reason=str(error), changed_at=time.time()),
            )
            raise
        self._registry.observe(peer_id, capabilities)
        self._registry.promote(peer_id, permissions=capabilities)
        self._set_state(peer_id, ConnectionState.online(now=time.time()))
        self._providers[peer_id] = provider
        return provider

    def disconnect(self, peer_id: NodeId, *, reason: str = "disconnected") -> None:
        self._providers.pop(peer_id, None)
        self._set_state(peer_id, ConnectionState.offline(reason, now=time.time()))

    def disconnect_all(self) -> None:
        for peer_id in tuple(self._providers):
            self.disconnect(peer_id)

    def _set_state(self, peer_id: NodeId, state: ConnectionState) -> None:
        if self._registry.record(peer_id) is not None:
            self._registry.set_connection(peer_id, state)

"""Outgoing connection lifecycle composed from trust, discovery, and transport owners."""

from __future__ import annotations

import time
from collections.abc import Callable, MutableMapping
from dataclasses import replace
from threading import RLock
from typing import Any, cast

from .connection_state import ConnectionState, ConnectionStatus
from .discovery import endpoint_rank
from .identity import NodeId, verify_transport_proof
from .models import DiscoveredNodeCandidate, EndpointCandidate
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
        provider_factory: Callable[..., Any] = AuthenticatedNodeProvider,
        persist: Callable[[], bool] | None = None,
        on_route_attempt: Callable[
            [str, EndpointCandidate, str, str | None], None
        ] | None = None,
    ) -> None:
        self._local_id = local_id
        self._pairing = pairing
        self._registry = registry
        self._candidates = candidates
        self._providers: dict[NodeId, AuthenticatedNodeProvider] = {}
        self._provider_factory = provider_factory
        self._persist = persist
        self._on_route_attempt = on_route_attempt
        self._generations: dict[NodeId, int] = {}
        self._resume_peers: set[NodeId] = set()
        self._lock = RLock()

    @property
    def providers(self) -> tuple[NodeId, ...]:
        with self._lock:
            return tuple(self._providers)

    def connection_generation(self, peer_id: NodeId) -> int:
        with self._lock:
            return self._generations.get(peer_id, 0)

    def is_current_generation(self, peer_id: NodeId, generation: int) -> bool:
        with self._lock:
            return self._generations.get(peer_id, 0) == generation

    def reconnect(self, peer_id: NodeId) -> AuthenticatedNodeProvider:
        """Replace the physical route while retaining the logical peer."""

        with self._lock:
            self._providers.pop(peer_id, None)
            self._resume_peers.add(peer_id)
            self._set_state(
                peer_id,
                ConnectionState(ConnectionStatus.RESUMING, changed_at=time.time()),
            )
        return self.connect(peer_id)

    def connect(self, peer_id: NodeId) -> AuthenticatedNodeProvider:
        with self._lock:
            existing = self._providers.get(peer_id)
            if existing is not None:
                return existing
            trusted = self._pairing.trusted.get(peer_id)
            candidate = self._candidates.get(peer_id.value)
            if trusted is None or candidate is None or not candidate.compatible:
                raise PermissionError("peer must be discovered and trusted first")
            endpoints = tuple(
                endpoint
                for endpoint in candidate.endpoint_candidates
                if endpoint.validation != "invalid"
            )
            if not endpoints:
                raise ConnectionError("peer has no connectable endpoint")
            if not self._candidate_generation_allowed(trusted, candidate):
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
            generation = self._generations.get(peer_id, 0) + 1
            self._generations[peer_id] = generation
            status = (
                ConnectionStatus.RESUMING
                if peer_id in self._resume_peers
                else ConnectionStatus.CONNECTING
            )
            self._resume_peers.discard(peer_id)
            self._registry.observe(peer_id, frozenset())
            self._set_state(peer_id, ConnectionState(status, changed_at=time.time()))
            errors: list[BaseException] = []
            for endpoint in sorted(endpoints, key=endpoint_rank):
                self._report_route_attempt("connection", endpoint, "started", None)
                try:
                    transport = TLSRemoteTransport(
                        endpoint.address,
                        endpoint.port,
                        expected_fingerprint=candidate.transport_fingerprint,
                    )
                    provider = self._provider_factory(
                        node_id=peer_id,
                        caller_node_id=self._local_id,
                        secret=trusted.secret,
                        transport=transport,
                    )
                    hello = provider.hello()
                    self._validate_hello_generation(trusted, candidate, hello)
                    capabilities = parse_hello_capabilities(hello)
                except RemoteAuthError as error:
                    self._report_route_attempt(
                        "connection", endpoint, "failed", str(error)
                    )
                    self._set_state(
                        peer_id,
                        ConnectionState(
                            ConnectionStatus.AUTHENTICATION_FAILED,
                            reason=str(error),
                            changed_at=time.time(),
                        ),
                    )
                    raise
                except (
                    OSError,
                    ConnectionError,
                    RemoteExecutionError,
                    RemoteTransportError,
                ) as error:
                    errors.append(error)
                    self._mark_failure(peer_id, endpoint)
                    self._report_route_attempt(
                        "connection", endpoint, "failed", str(error)
                    )
                    continue
                self._registry.observe(peer_id, capabilities)
                self._registry.promote(peer_id, permissions=capabilities)
                self._record_generation(peer_id, trusted, candidate)
                self._providers[peer_id] = provider
                self._set_state(peer_id, ConnectionState.online(now=time.time()))
                self._mark_success(peer_id, endpoint)
                self._report_route_attempt("connection", endpoint, "succeeded", None)
                return cast(AuthenticatedNodeProvider, provider)
            reason = str(errors[-1]) if errors else "all endpoint routes failed"
            self._providers.pop(peer_id, None)
            self._set_state(
                peer_id,
                ConnectionState.offline(reason, now=time.time()),
            )
            if errors:
                raise errors[-1]
            raise ConnectionError(reason)

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
            pass

    @staticmethod
    def _candidate_generation_allowed(
        trusted: Any, candidate: DiscoveredNodeCandidate
    ) -> bool:
        fingerprint = trusted.transport_fingerprint
        if fingerprint is None or candidate.transport_fingerprint == fingerprint:
            return True
        generation = candidate.transport_generation
        return bool(
            trusted.root_public_key
            and generation is not None
            and (
                trusted.transport_generation is None
                or generation > trusted.transport_generation
            )
            and candidate.transport_proof
            and verify_transport_proof(
                NodeId(candidate.stable_id),
                trusted.root_public_key,
                generation,
                candidate.transport_fingerprint or "",
                candidate.transport_proof,
            )
        )

    @staticmethod
    def _validate_hello_generation(
        trusted: Any,
        candidate: DiscoveredNodeCandidate,
        hello: dict[str, Any],
    ) -> None:
        continuity_fields = (
            "transport_fingerprint",
            "root_public_key",
            "transport_generation",
            "transport_proof",
        )
        if all(hello.get(field) is None for field in continuity_fields):
            if candidate.transport_fingerprint == trusted.transport_fingerprint:
                return
            raise RemoteAuthError("replacement transport has no continuity proof")
        for field in continuity_fields:
            expected = getattr(candidate, field)
            if expected is not None and hello.get(field) != expected:
                raise RemoteAuthError("hello transport continuity does not match route")
        if candidate.transport_fingerprint == trusted.transport_fingerprint:
            return
        if not ConnectionManager._candidate_generation_allowed(trusted, candidate):
            raise RemoteAuthError("transport continuity proof is invalid")

    def _record_generation(
        self, peer_id: NodeId, trusted: Any, candidate: DiscoveredNodeCandidate
    ) -> None:
        if candidate.transport_fingerprint == trusted.transport_fingerprint:
            return
        updated = replace(
            trusted,
            transport_fingerprint=candidate.transport_fingerprint,
            root_public_key=candidate.root_public_key,
            transport_generation=candidate.transport_generation,
            transport_proof=candidate.transport_proof,
        )
        self._pairing.trusted[peer_id] = updated
        if self._persist is not None and not self._persist():
            self._pairing.trusted[peer_id] = trusted
            raise RemoteAuthError("transport generation was not durably persisted")

    def disconnect(self, peer_id: NodeId, *, reason: str = "disconnected") -> None:
        with self._lock:
            self._generations[peer_id] = self._generations.get(peer_id, 0) + 1
            self._providers.pop(peer_id, None)
            self._set_state(peer_id, ConnectionState.offline(reason, now=time.time()))

    def disconnect_all(self) -> None:
        for peer_id in tuple(self._providers):
            self.disconnect(peer_id)

    def _set_state(self, peer_id: NodeId, state: ConnectionState) -> None:
        if self._registry.record(peer_id) is not None:
            self._registry.set_connection(peer_id, state)

    def _mark_success(self, peer_id: NodeId, endpoint: EndpointCandidate) -> None:
        candidate = self._candidates.get(peer_id.value)
        if candidate is None:
            return
        now = time.time()
        updated = tuple(
            replace(item, last_success=now) if item.key == endpoint.key else item
            for item in candidate.endpoint_candidates
        )
        self._candidates[peer_id.value] = replace(
            candidate, endpoint_candidates=updated
        )

    def _mark_failure(self, peer_id: NodeId, endpoint: EndpointCandidate) -> None:
        candidate = self._candidates.get(peer_id.value)
        if candidate is None:
            return
        now = time.time()
        updated = tuple(
            replace(item, last_failure=now) if item.key == endpoint.key else item
            for item in candidate.endpoint_candidates
        )
        self._candidates[peer_id.value] = replace(
            candidate, endpoint_candidates=updated
        )

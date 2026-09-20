"""Application-neutral composition root for the headless connection core."""

from __future__ import annotations

import hmac
import json
import logging
import platform
import socket
import threading
from collections.abc import Callable
from dataclasses import asdict, dataclass
from enum import Enum
from pathlib import Path
from types import TracebackType
from typing import Any

from typing_extensions import Self

from ._version import __version__
from .cluster import Cluster, ClusterRole
from .connection_manager import ConnectionManager
from .discovery_full import (
    REAP_TICK_SECONDS,
    SERVICE_TYPE,
    DiscoveryAdvertisement,
    DiscoveryBackend,
    NetworkDiscovery,
)
from .identity import (
    NodeId,
    NodeIdentity,
    TransportGenerationManager,
    TransportStateError,
    node_identity_fingerprint,
)
from .models import (
    READ_CAPABILITIES,
    READ_PERMISSIONS,
    DiscoveredNodeCandidate,
    NodeCapability,
    NodePermission,
)
from .pairing import PairingManager, PeerGrant, PendingPairing, TrustedPeer
from .pairing_flow import NetworkPairing
from .persistence import JsonStateStore, StateDataError, migrate_state
from .registry import NodeRegistry
from .remote_models import NodeStatus
from .remote_service import RemoteService
from .role_engine import ClusterRole as RegistryClusterRole
from .runtime_persistence import (
    peer_grant_from_json,
    peer_grant_to_json,
    pending_pairing_from_json,
    pending_pairing_to_json,
    trusted_peer_from_json,
    trusted_peer_to_json,
)
from .server import PEER_SERVICE_DEFAULT_PORT, RemoteSocketServer
from .sharing import CapabilityShare
from .tls_material import (
    ensure_tls_material,
    ensure_tls_material_generation,
    server_context,
)
from .wire_protocol import (
    CapabilityElevationRequest,
    PairingControlRequest,
    PairingRequest,
    RemoteRequest,
)
from .wire_protocol import (
    PeerGrant as WirePeerGrant,
)

LOGGER = logging.getLogger(__name__)


class PersistenceError(RuntimeError):
    """Raised when a trust transition cannot be durably committed."""


class RuntimeState(str, Enum):
    STOPPED = "stopped"
    STARTING = "starting"
    STARTED = "started"
    PERSISTENCE_FAILED = "persistence_failed"
    LISTENER_UNAVAILABLE = "listener_unavailable"


@dataclass(frozen=True, slots=True)
class RuntimeStatus:
    state: RuntimeState
    listener_started: bool = False
    discovery_started: bool = False
    discovery_disabled: bool = False
    discovery_reason: str | None = None
    bound_host: str | None = None
    bound_port: int | None = None
    preferred_port_honored: bool | None = None
    tls_fingerprint: str | None = None
    reason: str | None = None


@dataclass(frozen=True, slots=True)
class ConnectConfig:
    profile_dir: Path
    app_id: str = "expra-connect"
    display_name: str = "Expra Connect Peer"
    app_version: str = __version__
    bind_host: str = "0.0.0.0"
    preferred_port: int = PEER_SERVICE_DEFAULT_PORT
    discovery_enabled: bool = True
    service_type: str = SERVICE_TYPE
    hostname: str = ""
    platform_name: str = ""
    advertised_addresses: tuple[str, ...] | None = None
    cluster_enabled: bool = False
    capabilities: frozenset[NodeCapability] = READ_CAPABILITIES
    provider: Any = None
    on_discovery: Callable[[str, Any], None] | None = None
    on_pairing_request: Callable[[PairingRequest], bool] | None = None
    on_elevation_request: Callable[[CapabilityElevationRequest], bool] | None = None
    on_route_attempt: Callable[[str, Any, str, str | None], None] | None = None
    discovery_backend_factory: (
        Callable[[Callable[[str, str, Any], None]], DiscoveryBackend] | None
    ) = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "profile_dir", Path(self.profile_dir).expanduser())
        if not self.app_id.strip():
            raise ValueError("app_id must not be empty")
        if not self.service_type.endswith("."):
            raise ValueError("service_type must be a fully qualified service type")
        if not 0 <= self.preferred_port <= 65535:
            raise ValueError("preferred_port must be between 0 and 65535")
        if self.advertised_addresses is not None and (
            not self.advertised_addresses
            or not all(address.strip() for address in self.advertised_addresses)
        ):
            raise ValueError("advertised_addresses must contain non-empty values")
    @property
    def resolved_hostname(self) -> str:
        return self.hostname or socket.gethostname()
    @property
    def resolved_platform(self) -> str:
        return self.platform_name or platform.system().lower()
class ConnectRuntime:
    """Compose the mature peer components without doing work in construction."""

    def __init__(self, config: ConnectConfig) -> None:
        self.config = config
        self._identity: NodeIdentity | None = None
        self._pairing: PairingManager | None = None
        self._registry: NodeRegistry | None = None
        self._cluster: Cluster | None = None
        self._sharing = CapabilityShare()
        self._server: RemoteSocketServer | None = None
        self._service: RemoteService | None = None
        self._discovery: NetworkDiscovery | None = None
        self._expiry_stop: threading.Event | None = None
        self._expiry_thread: threading.Thread | None = None
        self._peers: dict[str, DiscoveredNodeCandidate] = {}
        self._connection_manager: ConnectionManager | None = None
        self._transport_generations: TransportGenerationManager | None = None
        self._network_pairing: NetworkPairing | None = None
        self._persistence_ready = False
        self._pending_permissions: dict[str, frozenset[str]] = {}
        self._lifecycle_lock = threading.RLock()
        self._pairing_transition_lock = threading.Lock()
        self._generation = 0
        self._status = RuntimeStatus(RuntimeState.STOPPED)

    @property
    def status(self) -> RuntimeStatus:
        return self._status
    @property
    def started(self) -> bool:
        return self._status.state is RuntimeState.STARTED

    @property
    def identity(self) -> NodeIdentity | None:
        return self._identity
    @property
    def registry(self) -> NodeRegistry | None:
        return self._registry
    @property
    def pairing(self) -> PairingManager | None:
        return self._pairing
    @property
    def sharing(self) -> CapabilityShare:
        return self._sharing
    @property
    def cluster(self) -> Cluster | None:
        return self._cluster
    @property
    def peers(self) -> tuple[DiscoveredNodeCandidate, ...]:
        return tuple(self._peers.values())

    @property
    def connections(self) -> tuple[NodeId, ...]:
        return self._connection_manager.providers if self._connection_manager else ()

    @property
    def transport_generations(self) -> TransportGenerationManager | None:
        return self._transport_generations

    def reconnect_peer(self, peer_id: NodeId) -> Any:
        """Reconnect a logical peer without changing trust or membership."""

        manager = self._connection_manager
        if manager is None:
            raise RuntimeError("runtime has not started")
        reconnect = getattr(manager, "reconnect", None)
        if callable(reconnect):
            return reconnect(peer_id)
        manager.disconnect(peer_id, reason="reconnect")
        return manager.connect(peer_id)

    def rotate_transport(self) -> RuntimeStatus:
        """Activate a root-authorized TLS generation and restart the listener."""
        if (
            not self.started
            or self._identity is None
            or self._transport_generations is None
        ):
            raise RuntimeError("runtime must be started before transport rotation")
        next_generation = self._transport_generations.current.generation + 1
        material = ensure_tls_material_generation(
            self.config.profile_dir, self._identity.node_id.value, next_generation
        )
        self._transport_generations.prepare(material.fingerprint)
        self._transport_generations.activate(next_generation)
        self.shutdown()
        status = self.start()
        if status.state is not RuntimeState.STARTED:
            self._transport_generations.rollback_active()
            self.start()
            raise TransportStateError("transport rotation could not restart listener")
        return status

    def diagnostics(self) -> dict[str, Any]:
        """Return operational state without credentials or invitation material."""

        identity = self._identity
        generations = self._transport_generations
        result: dict[str, Any] = {
            "status": asdict(self._status),
            "generation": self._generation,
            "identity": None,
            "transport": {
                "fingerprint": self._status.tls_fingerprint,
                "current_generation": (
                    generations.current_generation if generations is not None else None
                ),
                "pending_generation": (
                    generations.next.generation
                    if generations is not None and generations.next is not None
                    else None
                ),
            },
            "routes": [],
            "connections": [],
            "sessions": [],
        }
        if identity is not None:
            result["identity"] = {
                "node_id": identity.node_id.value,
                "root_fingerprint": node_identity_fingerprint(identity.root_public_key),
            }
        for candidate in self.peers:
            result["routes"].append(
                {
                    "node_id": candidate.stable_id,
                    "hostname": candidate.hostname,
                    "addresses": candidate.addresses,
                    "port": candidate.port,
                    "transport_fingerprint": candidate.transport_fingerprint,
                    "last_seen": candidate.last_seen,
                }
            )
        if self._registry is not None:
            for record in self._registry.records:
                result["connections"].append(
                    {
                        "node_id": record.node_id.value,
                        "state": record.connection.status.value,
                        "reason": record.connection.reason,
                        "changed_at": record.connection.changed_at,
                    }
                )
        if self._connection_manager is not None:
            result["sessions"] = [
                node_id.value for node_id in self._connection_manager.providers
            ]
        return result

    def connect_peer(self, peer_id: NodeId) -> Any:
        manager = self._connection_manager
        if manager is None:
            raise RuntimeError("runtime has not started")
        return manager.connect(peer_id)

    def disconnect_peer(self, peer_id: NodeId, *, reason: str = "disconnected") -> None:
        """Forget one outgoing provider while retaining trust and membership."""

        if self._connection_manager is not None:
            self._connection_manager.disconnect(peer_id, reason=reason)

    def pair_peer(
        self,
        peer_id: NodeId,
        *,
        permissions: frozenset[str] = frozenset({NodePermission.READ_STATE.value}),
        cancel_event: threading.Event | None = None,
    ) -> TrustedPeer:
        """Complete an explicit network pairing with a discovered candidate."""

        pairing = self._network_pairing
        if pairing is None:
            raise RuntimeError("runtime must be started before pairing")
        return pairing.pair(peer_id, permissions=permissions, cancel_event=cancel_event)

    def begin_pairing(self, peer_id: NodeId, **kwargs: Any) -> PendingPairing:
        pairing = self._require_pairing()
        return pairing.begin(peer_id, **kwargs)

    def approve_pairing(
        self, transaction_id: str, permissions: frozenset[str]
    ) -> PeerGrant:
        allowed = frozenset(permission.value for permission in READ_PERMISSIONS)
        if not permissions <= allowed:
            raise ValueError("pairing approval permissions must be read-only")
        pairing = self._require_pairing()
        previous = dict(pairing.grants)
        grant = pairing.approve(transaction_id, permissions)
        if not self._save_persisted_state():
            pairing.grants = previous
            raise PersistenceError("pairing approval was not durably persisted")
        self._refresh_live_grants()
        return grant

    def revoke_peer(self, peer_id: NodeId, *, _refresh: bool = True) -> None:
        pairing = self._require_pairing()
        previous_trusted = dict(pairing.trusted)
        previous_grants = dict(pairing.grants)
        previous_pending = dict(pairing.pending)
        pairing.revoke(peer_id)
        if not self._save_persisted_state():
            pairing.trusted = previous_trusted
            pairing.grants = previous_grants
            pairing.pending = previous_pending
            raise PersistenceError("peer revocation was not durably persisted")
        if self._registry is not None and self._registry.record(peer_id) is not None:
            self._registry.revoke(peer_id)
        self._peers.pop(peer_id.value, None)
        if self._connection_manager is not None:
            self._connection_manager.disconnect(peer_id, reason="revoked")
        if _refresh:
            self._refresh_live_grants()

    def _revoke_self(self, peer_id: NodeId) -> dict[str, Any]:
        return (
            self.revoke_peer(peer_id, _refresh=False),
            {"revoked": True, "node_id": peer_id.value},
        )[1]

    def _require_pairing(self) -> PairingManager:
        if self._pairing is None:
            raise RuntimeError("runtime has not started")
        return self._pairing

    def start(self) -> RuntimeStatus:
        with self._lifecycle_lock:
            return self._start_locked()

    def _start_locked(self) -> RuntimeStatus:
        if self._status.state is RuntimeState.STARTED:
            return self._status
        self._generation += 1
        generation = self._generation
        self._status = RuntimeStatus(RuntimeState.STARTING)
        try:
            self._load_identity()
            self._load_persisted_state()
            return self._start_components(generation)
        except (KeyError, OSError, TypeError, ValueError, StateDataError) as error:
            self._status = RuntimeStatus(
                RuntimeState.PERSISTENCE_FAILED, reason=str(error)
            )
            self._clear_components()
            return self._status

    def shutdown(self) -> None:
        with self._lifecycle_lock:
            self._shutdown_locked()

    def _shutdown_locked(self) -> None:
        self._generation += 1
        if self._connection_manager is not None:
            self._connection_manager.disconnect_all()
        self._save_persisted_state()
        self._stop_expiry_worker()
        discovery, server = self._discovery, self._server
        self._discovery = None
        self._server = None
        self._service = None
        self._connection_manager = None
        self._network_pairing = None
        self._peers.clear()
        if discovery is not None:
            discovery.stop()
        if server is not None:
            server.stop()
        self._status = RuntimeStatus(RuntimeState.STOPPED)

    def __enter__(self) -> Self:
        self.start()
        return self

    def __exit__(
        self,
        _type: type[BaseException] | None,
        _value: BaseException | None,
        _traceback: TracebackType | None,
    ) -> None:
        self.shutdown()

    def _load_identity(self) -> None:
        profile = self.config.profile_dir
        path = profile / "identity.json"
        if path.exists():
            document = JsonStateStore(path, kind="identity").load()
            self._identity = NodeIdentity.from_json(json.dumps(document))
            if (
                document.get("schema_version") != 2
                or "root_private_key" not in document
            ):
                self._identity.save(path)
        else:
            self._identity = NodeIdentity.create()
            self._identity.save(path)

    def _load_persisted_state(self) -> None:
        if self._identity is None:
            raise RuntimeError("identity must be loaded first")
        self._persistence_ready = False
        self._pairing = PairingManager(self._identity.node_id)
        self._registry = NodeRegistry(
            self._identity.node_id, cluster_enabled=self.config.cluster_enabled
        )
        if self.config.cluster_enabled:
            cluster_store = JsonStateStore(
                self.config.profile_dir / "cluster.json", kind="cluster"
            )
            self._cluster = (
                Cluster.load(self._identity.node_id, cluster_store)
                if cluster_store.path.exists()
                else Cluster(self._identity.node_id)
            )
        state = JsonStateStore(self.config.profile_dir / "trust.json", kind="trust")
        if state.path.exists():
            document = state.load()
            for raw in document.get("grants", []):
                grant = peer_grant_from_json(raw)
                self._pairing.grants[grant.caller_id] = grant
            for raw in document.get("trusted", []):
                trusted = trusted_peer_from_json(raw)
                self._pairing.trusted[trusted.peer_id] = trusted
            for raw in document.get("pending", []):
                pending, permissions = pending_pairing_from_json(raw)
                self._pairing.pending[pending.transaction_id] = pending
                self._pending_permissions[pending.transaction_id] = permissions
            self._wire_grants()
        self._connection_manager = ConnectionManager(
            local_id=self._identity.node_id,
            pairing=self._pairing,
            registry=self._registry,
            candidates=self._peers,
            persist=self._save_persisted_state,
            on_route_attempt=self.config.on_route_attempt,
        )
        self._transport_generations = TransportGenerationManager(
            self._identity,
            store=JsonStateStore(self.config.profile_dir / "transport.json"),
        )
        self._persistence_ready = True

    def _start_components(self, generation: int) -> RuntimeStatus:
        if self._identity is None or self._pairing is None:
            raise RuntimeError("runtime state is incomplete")
        profile = self.config.profile_dir
        self._transport_generations = (
            self._transport_generations
            or TransportGenerationManager(
                self._identity,
                store=JsonStateStore(profile / "transport.json"),
            )
        )
        if self._transport_generations.current_generation is None:
            material = ensure_tls_material(profile, self._identity.node_id.value)
            self._transport_generations.initialize(material.fingerprint)
        else:
            generation = self._transport_generations.current.generation
            legacy = ensure_tls_material(profile, self._identity.node_id.value)
            material = (
                legacy
                if generation == 1
                else ensure_tls_material_generation(
                    profile, self._identity.node_id.value, generation
                )
            )
        if self._transport_generations.current.fingerprint != material.fingerprint:
            raise TransportStateError("TLS material does not match trusted generation")
        service = RemoteService(
            node_id=self._identity.node_id,
            display_name=self.config.display_name,
            hostname=self.config.resolved_hostname,
            platform=self.config.resolved_platform,
            status=NodeStatus.ONLINE,
            capabilities=(
                self.config.capabilities | {NodeCapability.REMOTE_MANAGEMENT}
                if self._cluster is not None
                else self.config.capabilities
            ),
            provider=(
                self.config.provider if self.config.provider is not None else object()
            ),
            secret=self._identity.secret,
            app_version=self.config.app_version,
            grants=self._wire_grants(),
            identity_fingerprint=node_identity_fingerprint(self._identity.node_id),
            transport_fingerprint=material.fingerprint,
            root_public_key=self._identity.root_public_key,
            transport_generation=self._transport_generations.current.generation,
            transport_proof=self._transport_generations.current.proof,
            capability_share=self._sharing,
            cluster_id=self._cluster.cluster_id if self._cluster is not None else None,
            coordinator_epoch=(self._cluster.epoch.epoch if self._cluster else None),
            fencing_token=(
                self._cluster.epoch.fencing_token if self._cluster else None
            ),
            role_handler=self._handle_role_request
            if self._cluster is not None
            else None,
            trust_revoke_handler=self._revoke_self,
            trust_revoke_commit=self._refresh_live_grants,
        )
        server = RemoteSocketServer(
            service,
            host=self.config.bind_host,
            preferred_port=self.config.preferred_port,
            ssl_context=server_context(material),
            pairing_handler=self._handle_pairing_request,
            pair_confirm_handler=self._handle_pairing_control,
            pair_abort_handler=self._handle_pairing_control,
            elevation_handler=self._handle_elevation_request,
        )
        try:
            server.start()
        except OSError as error:
            self._clear_components()
            self._status = RuntimeStatus(
                RuntimeState.LISTENER_UNAVAILABLE, reason=str(error)
            )
            return self._status
        self._server = server
        self._service = service
        self._network_pairing = NetworkPairing(
            identity=self._identity,
            transport_fingerprint=material.fingerprint,
            root_public_key=self._identity.root_public_key,
            transport_generation=self._transport_generations.current.generation,
            transport_proof=self._transport_generations.current.proof,
            pairing=self._pairing,
            candidates=self._peers,
            persist=self._save_persisted_state,
            on_route_attempt=self.config.on_route_attempt,
        )
        bound_port = server.bound_port
        advertisement = DiscoveryAdvertisement(
            stable_id=self._identity.node_id.value,
            display_name=self.config.display_name,
            hostname=self.config.resolved_hostname,
            app_version=self.config.app_version,
            platform=self.config.resolved_platform,
            connectable=bound_port is not None,
            port=bound_port,
            identity_fingerprint=node_identity_fingerprint(self._identity.node_id),
            transport_fingerprint=material.fingerprint,
            root_public_key=self._identity.root_public_key,
            transport_generation=self._transport_generations.current.generation,
            transport_proof=self._transport_generations.current.proof,
            advertised_addresses=self.config.advertised_addresses,
        )
        if not self.config.discovery_enabled:
            return self._started_status(server, material.fingerprint, False, True, None)
        try:
            discovery = NetworkDiscovery(
                self._identity.node_id,
                advertisement=advertisement,
                backend_factory=self.config.discovery_backend_factory,
                service_type=self.config.service_type,
                on_event=lambda kind, payload: self._on_discovery(
                    generation, kind, payload
                ),
            )
        except Exception as error:  # noqa: BLE001 - discovery is optional.
            return self._started_status(
                server,
                material.fingerprint,
                False,
                False,
                f"Discovery setup failed: {error}",
            )
        if discovery.start():
            self._discovery = discovery
            self._start_expiry_worker(generation, discovery)
            return self._started_status(server, material.fingerprint, True, False, None)
        self._discovery = discovery
        return self._started_status(
            server, material.fingerprint, False, False, discovery.unavailable_reason
        )

    def _started_status(
        self,
        server: RemoteSocketServer,
        fingerprint: str,
        discovery_started: bool,
        discovery_disabled: bool,
        discovery_reason: str | None,
    ) -> RuntimeStatus:
        self._status = RuntimeStatus(
            RuntimeState.STARTED,
            listener_started=True,
            discovery_started=discovery_started,
            discovery_disabled=discovery_disabled,
            discovery_reason=discovery_reason,
            bound_host=self.config.bind_host,
            bound_port=server.bound_port,
            preferred_port_honored=server.preferred_port_honored,
            tls_fingerprint=fingerprint,
        )
        return self._status

    def _on_discovery(self, generation: int, kind: str, payload: Any) -> None:
        with self._lifecycle_lock:
            if generation != self._generation or not self.started:
                return
            if kind == "candidate" and isinstance(payload, DiscoveredNodeCandidate):
                if not self._trusted_candidate_is_valid(payload):
                    return
                self._peers[payload.stable_id] = payload
                if self._registry is not None:
                    self._registry.observe(NodeId(payload.stable_id), frozenset())
            elif kind == "lost":
                peer_id = str(payload)
                self._peers.pop(peer_id, None)
                if self._registry is not None:
                    try:
                        self.disconnect_peer(NodeId(peer_id), reason="peer disappeared")
                    except ValueError:
                        pass
            if self.config.on_discovery is not None:
                self.config.on_discovery(kind, payload)

    def _trusted_candidate_is_valid(self, candidate: DiscoveredNodeCandidate) -> bool:
        if self._pairing is None:
            return False
        try:
            peer_id = NodeId(candidate.stable_id)
        except ValueError:
            return False
        trusted = self._pairing.trusted.get(peer_id)
        grant = self._pairing.grants.get(peer_id)
        expected_identity = (
            trusted.identity_fingerprint
            if trusted is not None
            else grant.identity_fingerprint
            if grant is not None
            else None
        )
        expected_transport = (
            trusted.transport_fingerprint
            if trusted is not None
            else grant.transport_fingerprint
            if grant is not None
            else None
        )
        if (
            expected_identity is not None
            and candidate.identity_fingerprint != expected_identity
        ):
            return False
        return not (
            expected_transport is not None
            and candidate.transport_fingerprint != expected_transport
        )

    def _handle_pairing_request(self, request: PairingRequest) -> dict[str, Any]:
        callback = self.config.on_pairing_request
        if callback is None or not callback(request):
            return {"approved": False}
        pairing = self._require_pairing()
        pending = pairing.begin(
            request.caller_node_id,
            secret=request.proposed_secret,
            identity_fingerprint=request.identity_fingerprint,
            transport_fingerprint=request.transport_fingerprint,
            root_public_key=request.root_public_key,
            transport_generation=request.transport_generation,
            transport_proof=request.transport_proof,
        )
        self._pending_permissions[pending.transaction_id] = frozenset(
            permission.value for permission in request.permissions
        )
        if not self._save_persisted_state():
            pairing.abort(pending.transaction_id)
            self._pending_permissions.pop(pending.transaction_id, None)
            return {"approved": False}
        return {
            "approved": True,
            "transaction_id": pending.transaction_id,
            "caller_node_id": pending.peer_id.value,
            "identity_fingerprint": request.identity_fingerprint,
            "transport_fingerprint": request.transport_fingerprint,
            "root_public_key": request.root_public_key,
            "transport_generation": request.transport_generation,
            "transport_proof": request.transport_proof,
            "secret": pending.secret,
            "permissions": sorted(self._pending_permissions[pending.transaction_id]),
            "expires_at": pending.expires_at,
        }

    def _handle_pairing_control(self, request: PairingControlRequest) -> bool:
        with self._pairing_transition_lock:
            return self._handle_pairing_control_locked(request)

    def _handle_pairing_control_locked(self, request: PairingControlRequest) -> bool:
        pairing = self._require_pairing()
        pending = pairing.pending.get(request.transaction_id)
        expected_permissions = self._pending_permissions.get(request.transaction_id)
        if (
            pending is None
            or expected_permissions is None
            or expected_permissions
            != frozenset(permission.value for permission in request.permissions)
        ):
            return False
        if (
            pending.peer_id != request.caller_node_id
            or pending.identity_fingerprint != request.identity_fingerprint
            or pending.transport_fingerprint != request.transport_fingerprint
            or pending.secret != request.secret
            or pending.expires_at != request.expires_at
        ):
            return False
        if request.operation == "pair_abort":
            pairing.abort(request.transaction_id)
            self._pending_permissions.pop(request.transaction_id, None)
            return self._save_persisted_state()
        if request.operation != "pair_confirm":
            return False
        previous_grants = dict(pairing.grants)
        previous_pending = dict(pairing.pending)
        try:
            self.approve_pairing(request.transaction_id, expected_permissions)
        except (PersistenceError, ValueError):
            return False
        pairing.pending.pop(request.transaction_id, None)
        self._pending_permissions.pop(request.transaction_id, None)
        if self._save_persisted_state():
            return True
        pairing.grants = previous_grants
        pairing.pending = previous_pending
        self._pending_permissions[request.transaction_id] = expected_permissions
        self._save_persisted_state()
        return False

    def _handle_elevation_request(self, request: CapabilityElevationRequest) -> bool:
        callback = self.config.on_elevation_request
        pairing = self._require_pairing()
        grant = pairing.grants.get(request.caller_node_id)
        if (
            callback is None
            or grant is None
            or not hmac.compare_digest(grant.secret, request.current_secret)
            or grant.identity_fingerprint != request.identity_fingerprint
            or grant.transport_fingerprint != request.transport_fingerprint
        ):
            return False
        if not callback(request):
            return False
        previous = grant
        pairing.grants[request.caller_node_id] = PeerGrant(
            caller_id=grant.caller_id,
            secret=grant.secret,
            permissions=frozenset(
                permission.value for permission in request.permissions
            ),
            identity_fingerprint=grant.identity_fingerprint,
            transport_fingerprint=grant.transport_fingerprint,
        )
        if not self._save_persisted_state():
            pairing.grants[request.caller_node_id] = previous
            return False
        if self._service is not None:
            self._service.update_grants(self._wire_grants())
        return True

    def _handle_role_request(self, request: RemoteRequest) -> dict[str, Any]:
        cluster = self._cluster
        if cluster is None:
            raise RuntimeError("cluster participation is disabled")
        caller = request.caller_node_id
        if caller is None:
            raise PermissionError("cluster operation has no caller identity")
        if request.op == "consume_invite":
            previous_assignment = cluster.assignments.get(caller)
            previous_online_state = cluster._online.get(caller)
            invite = cluster.consume_invite(request.params["token"], caller)
            if not self._save_persisted_state():
                cluster._invites[invite.token] = invite
                cluster._used_invites.discard(invite.token)
                if previous_assignment is None:
                    cluster.assignments.pop(caller, None)
                else:
                    cluster.assignments[caller] = previous_assignment
                if previous_online_state is None:
                    cluster._online.pop(caller, None)
                else:
                    cluster._online[caller] = previous_online_state
                raise PersistenceError("cluster invite was not durably persisted")
            return {
                "target_node_id": invite.target_id.value,
                "expires_at": invite.expires_at,
                "cluster_id": cluster.cluster_id,
                "coordinator_id": cluster.coordinator_id.value,
                "epoch": cluster.epoch.epoch,
                "fencing_token": cluster.epoch.fencing_token,
            }
        if (
            request.op
            in {
                "assign_role",
                "revoke_member",
                "renew_coordinator_lease",
            }
            and caller != cluster.coordinator_id
        ):
            raise PermissionError("only the Coordinator may perform this operation")
        if request.op == "renew_coordinator_lease":
            return {
                "ok": True,
                "cluster_id": cluster.cluster_id,
                "epoch": cluster.epoch.epoch,
                "coordinator_id": cluster.coordinator_id.value,
            }
        if request.op == "assign_role":
            target = NodeId(request.params["target_node_id"])
            roles = request.params["roles"]
            if len(roles) != 1:
                raise ValueError("one cluster role is required")
            previous_assignments = dict(cluster.assignments)
            previous_online_map = dict(cluster._online)
            cluster.assign(target, ClusterRole(roles[0]))
            if not self._save_persisted_state():
                cluster.assignments = previous_assignments
                cluster._online = previous_online_map
                raise PersistenceError("cluster role was not durably persisted")
            if self._registry is not None and self._registry.record(target) is not None:
                self._registry.join(
                    target,
                    role=RegistryClusterRole(roles[0]),
                    coordinator_id=cluster.coordinator_id,
                )
            return {"ok": True, "node_id": target.value, "role": roles[0]}
        if request.op == "revoke_member":
            target = NodeId(request.params["target_node_id"])
            previous_assignments = dict(cluster.assignments)
            cluster.revoke(target)
            if not self._save_persisted_state():
                cluster.assignments = previous_assignments
                raise PersistenceError("cluster revocation was not durably persisted")
            return {"ok": True, "node_id": target.value, "revoked": True}
        raise ValueError(f"unsupported cluster operation: {request.op}")

    def _start_expiry_worker(
        self, generation: int, discovery: NetworkDiscovery
    ) -> None:
        stop = threading.Event()
        self._expiry_stop = stop
        self._expiry_thread = threading.Thread(
            target=self._expiry_loop,
            args=(generation, discovery, stop),
            name="expra-connect-discovery-reaper",
            daemon=True,
        )
        self._expiry_thread.start()

    def _expiry_loop(
        self,
        generation: int,
        discovery: NetworkDiscovery,
        stop: threading.Event,
    ) -> None:
        while not stop.wait(REAP_TICK_SECONDS):
            if generation != self._generation or not self.started:
                return
            discovery.expire_stale()
            pairing = self._pairing
            if pairing is not None and pairing.prune_expired():
                expired = set(self._pending_permissions) - set(pairing.pending)
                for transaction_id in expired:
                    self._pending_permissions.pop(transaction_id, None)
                self._save_persisted_state()

    def _stop_expiry_worker(self) -> None:
        stop, thread = self._expiry_stop, self._expiry_thread
        self._expiry_stop = None
        self._expiry_thread = None
        if stop is not None:
            stop.set()
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=1.0)

    def _clear_components(self) -> None:
        self._stop_expiry_worker()
        if self._connection_manager is not None:
            self._connection_manager.disconnect_all()
        if self._discovery is not None:
            self._discovery.stop()
        if self._server is not None:
            self._server.stop()
        self._discovery = None
        self._server = None
        self._service = None
        self._connection_manager = None
        self._network_pairing = None

    def _save_persisted_state(self) -> bool:
        if self._pairing is None or not self._persistence_ready:
            return False
        saved = True
        try:
            JsonStateStore(self.config.profile_dir / "trust.json").save(
                migrate_state(
                    "trust",
                    {
                        "schema_version": 2,
                        "grants": [
                            peer_grant_to_json(item)
                            for item in self._pairing.grants.values()
                        ],
                        "trusted": [
                            trusted_peer_to_json(item)
                            for item in self._pairing.trusted.values()
                        ],
                        "pending": [
                            pending_pairing_to_json(
                                item,
                                self._pending_permissions.get(
                                    item.transaction_id, frozenset()
                                ),
                            )
                            for item in self._pairing.pending.values()
                        ],
                    },
                )
            )
        except (OSError, TypeError, ValueError) as error:
            LOGGER.warning("Could not persist connection trust state: %s", error)
            saved = False
        if self._cluster is not None:
            try:
                JsonStateStore(self.config.profile_dir / "cluster.json").save(
                    migrate_state("cluster", self._cluster.to_dict())
                )
            except (OSError, TypeError, ValueError) as error:
                LOGGER.warning("Could not persist cluster state: %s", error)
                saved = False
        return saved

    def _wire_grants(self) -> dict[NodeId, WirePeerGrant]:
        if self._pairing is None:
            return {}
        return {
            peer_id: WirePeerGrant(
                caller_node_id=grant.caller_id,
                secret=grant.secret,
                permissions=frozenset(
                    NodePermission(item) for item in grant.permissions
                ),
            )
            for peer_id, grant in self._pairing.grants.items()
        }

    def _refresh_live_grants(self) -> None:
        if self._service is not None:
            self._service.update_grants(self._wire_grants())

"""Application-neutral composition root for the headless connection core."""

from __future__ import annotations

import logging
import platform
import socket
import threading
from collections.abc import Callable
from dataclasses import dataclass
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
from .identity import NodeId, NodeIdentity, node_identity_fingerprint
from .models import (
    READ_CAPABILITIES,
    READ_PERMISSIONS,
    DiscoveredNodeCandidate,
    NodeCapability,
    NodePermission,
)
from .pairing import PairingManager, PeerGrant, PendingPairing, TrustedPeer
from .pairing_flow import NetworkPairing
from .persistence import JsonStateStore
from .registry import NodeRegistry
from .remote_models import NodeStatus
from .remote_service import RemoteService
from .role_engine import ClusterRole as RegistryClusterRole
from .server import PEER_SERVICE_DEFAULT_PORT, RemoteSocketServer
from .sharing import CapabilityShare
from .tls_material import ensure_tls_material, server_context
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
    cluster_enabled: bool = False
    capabilities: frozenset[NodeCapability] = READ_CAPABILITIES
    provider: Any = None
    on_discovery: Callable[[str, Any], None] | None = None
    on_pairing_request: Callable[[PairingRequest], bool] | None = None
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
        self._network_pairing: NetworkPairing | None = None
        self._persistence_ready = False
        self._pending_permissions: dict[str, frozenset[str]] = {}
        self._lifecycle_lock = threading.RLock()
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
    ) -> TrustedPeer:
        """Complete an explicit network pairing with a discovered candidate."""

        pairing = self._network_pairing
        if pairing is None:
            raise RuntimeError("runtime must be started before pairing")
        return pairing.pair(peer_id, permissions=permissions)

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

    def revoke_peer(self, peer_id: NodeId) -> None:
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
        self._refresh_live_grants()

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
        except (KeyError, OSError, TypeError, ValueError) as error:
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
        self._identity = (
            NodeIdentity.load(path) if path.exists() else NodeIdentity.create()
        )
        if not path.exists():
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
            cluster_store = JsonStateStore(self.config.profile_dir / "cluster.json")
            self._cluster = (
                Cluster.load(self._identity.node_id, cluster_store)
                if cluster_store.path.exists()
                else Cluster(self._identity.node_id)
            )
        state = JsonStateStore(self.config.profile_dir / "trust.json")
        if state.path.exists():
            document = state.load()
            for raw in document.get("grants", []):
                grant = _peer_grant_from_json(raw)
                self._pairing.grants[grant.caller_id] = grant
            for raw in document.get("trusted", []):
                trusted = _trusted_peer_from_json(raw)
                self._pairing.trusted[trusted.peer_id] = trusted
            for raw in document.get("pending", []):
                pending, permissions = _pending_pairing_from_json(raw)
                self._pairing.pending[pending.transaction_id] = pending
                self._pending_permissions[pending.transaction_id] = permissions
            self._wire_grants()
        self._connection_manager = ConnectionManager(
            local_id=self._identity.node_id,
            pairing=self._pairing,
            registry=self._registry,
            candidates=self._peers,
        )
        self._persistence_ready = True

    def _start_components(self, generation: int) -> RuntimeStatus:
        if self._identity is None or self._pairing is None:
            raise RuntimeError("runtime state is incomplete")
        profile = self.config.profile_dir
        material = ensure_tls_material(profile, self._identity.node_id.value)
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
            capability_share=self._sharing,
            cluster_id=self._cluster.cluster_id if self._cluster is not None else None,
            coordinator_epoch=(self._cluster.epoch.epoch if self._cluster else None),
            fencing_token=(
                self._cluster.epoch.fencing_token if self._cluster else None
            ),
            role_handler=self._handle_role_request
            if self._cluster is not None
            else None,
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
            pairing=self._pairing,
            candidates=self._peers,
            persist=self._save_persisted_state,
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
            "secret": pending.secret,
            "permissions": sorted(self._pending_permissions[pending.transaction_id]),
            "expires_at": pending.expires_at,
        }

    def _handle_pairing_control(self, request: PairingControlRequest) -> bool:
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
        try:
            self.approve_pairing(request.transaction_id, expected_permissions)
        except (PersistenceError, ValueError):
            return False
        self._pending_permissions.pop(request.transaction_id, None)
        return True

    def _handle_elevation_request(self, _request: CapabilityElevationRequest) -> bool:
        return False

    def _handle_role_request(self, request: RemoteRequest) -> dict[str, Any]:
        cluster = self._cluster
        if cluster is None:
            raise RuntimeError("cluster participation is disabled")
        if request.op == "renew_coordinator_lease":
            return {
                "cluster_id": cluster.cluster_id,
                "epoch": cluster.epoch.epoch,
                "coordinator_id": cluster.coordinator_id.value,
            }
        if request.op == "assign_role":
            target = NodeId(request.params["target_node_id"])
            roles = request.params["roles"]
            if len(roles) != 1:
                raise ValueError("one cluster role is required")
            cluster.assign(target, ClusterRole(roles[0]))
            if self._registry is not None and self._registry.record(target) is not None:
                self._registry.join(
                    target,
                    role=RegistryClusterRole(roles[0]),
                    coordinator_id=cluster.coordinator_id,
                )
            if not self._save_persisted_state():
                raise PersistenceError("cluster role was not durably persisted")
            return {"ok": True, "node_id": target.value, "role": roles[0]}
        if request.op == "revoke_member":
            target = NodeId(request.params["target_node_id"])
            cluster.revoke(target)
            if not self._save_persisted_state():
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
                {
                    "grants": [
                        _peer_grant_to_json(item)
                        for item in self._pairing.grants.values()
                    ],
                    "trusted": [
                        _trusted_peer_to_json(item)
                        for item in self._pairing.trusted.values()
                    ],
                    "pending": [
                        _pending_pairing_to_json(
                            item,
                            self._pending_permissions.get(
                                item.transaction_id, frozenset()
                            ),
                        )
                        for item in self._pairing.pending.values()
                    ],
                }
            )
        except (OSError, TypeError, ValueError) as error:
            LOGGER.warning("Could not persist connection trust state: %s", error)
            saved = False
        if self._cluster is not None:
            try:
                self._cluster.save(
                    JsonStateStore(self.config.profile_dir / "cluster.json")
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


def _peer_grant_to_json(grant: PeerGrant) -> dict[str, Any]:
    return {
        "caller_id": grant.caller_id.value,
        "secret": grant.secret,
        "permissions": sorted(grant.permissions),
        "identity_fingerprint": grant.identity_fingerprint,
        "transport_fingerprint": grant.transport_fingerprint,
    }


def _peer_grant_from_json(value: Any) -> PeerGrant:
    if not isinstance(value, dict):
        raise TypeError("persisted grant must be an object")
    return PeerGrant(
        caller_id=NodeId(str(value["caller_id"])),
        secret=str(value["secret"]),
        permissions=frozenset(str(item) for item in value["permissions"]),
        identity_fingerprint=_optional_text(value.get("identity_fingerprint")),
        transport_fingerprint=_optional_text(value.get("transport_fingerprint")),
    )


def _trusted_peer_to_json(peer: TrustedPeer) -> dict[str, Any]:
    return {
        "peer_id": peer.peer_id.value,
        "secret": peer.secret,
        "permissions": sorted(peer.permissions),
        "identity_fingerprint": peer.identity_fingerprint,
        "transport_fingerprint": peer.transport_fingerprint,
    }


def _trusted_peer_from_json(value: Any) -> TrustedPeer:
    if not isinstance(value, dict):
        raise TypeError("persisted trusted peer must be an object")
    return TrustedPeer(
        peer_id=NodeId(str(value["peer_id"])),
        secret=str(value["secret"]),
        permissions=frozenset(str(item) for item in value["permissions"]),
        identity_fingerprint=_optional_text(value.get("identity_fingerprint")),
        transport_fingerprint=_optional_text(value.get("transport_fingerprint")),
    )


def _pending_pairing_to_json(
    pending: PendingPairing, permissions: frozenset[str]
) -> dict[str, Any]:
    return {
        "transaction_id": pending.transaction_id,
        "peer_id": pending.peer_id.value,
        "secret": pending.secret,
        "expires_at": pending.expires_at,
        "identity_fingerprint": pending.identity_fingerprint,
        "transport_fingerprint": pending.transport_fingerprint,
        "permissions": sorted(permissions),
    }


def _pending_pairing_from_json(
    value: Any,
) -> tuple[PendingPairing, frozenset[str]]:
    if not isinstance(value, dict):
        raise TypeError("persisted pending pairing must be an object")
    expires_at = value["expires_at"]
    if not isinstance(expires_at, (int, float)):
        raise TypeError("persisted pending pairing expiry is invalid")
    permissions = value.get("permissions", [])
    if not isinstance(permissions, list) or any(
        not isinstance(item, str) for item in permissions
    ):
        raise TypeError("persisted pending pairing permissions are invalid")
    return (
        PendingPairing(
            transaction_id=str(value["transaction_id"]),
            peer_id=NodeId(str(value["peer_id"])),
            secret=str(value["secret"]),
            expires_at=float(expires_at),
            identity_fingerprint=_optional_text(value.get("identity_fingerprint")),
            transport_fingerprint=_optional_text(value.get("transport_fingerprint")),
        ),
        frozenset(permissions),
    )


def _optional_text(value: Any) -> str | None:
    return None if value is None else str(value)

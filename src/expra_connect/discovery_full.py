"""Local-network presence discovery for System Analyzer nodes.

One focused component whose only responsibility is discovering other running
System Analyzer installations on the same local network and emitting normalized
presence candidates. It advertises minimal presence metadata, browses for peers,
deduplicates, filters the local instance, and tracks TTL/expiry.

It deliberately does NOT:

- trust, authorise, or manage peers (a candidate is just an observation);
- retrieve CPU/RAM/process or health data;
- execute remote commands or stop remote processes;
- store credentials;
- own the node registry or UI;
- require Linux Avahi (python-zeroconf handles mDNS directly, when installed).

The wheel installs ``zeroconf``. When it is missing from an incomplete source
environment, the component reports itself unavailable and the application
continues as a normal single-node app.

Testability: the ``backend`` is injected. Tests never need a real LAN; they
drive a fake backend that calls the listener with synthetic service records.
"""

import ipaddress
import logging
import socket
import time
import warnings
from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from threading import RLock
from typing import Any, Protocol, cast

from .discovery_backend import _service_address_texts
from .models import DiscoveredNodeCandidate

LOGGER = logging.getLogger(__name__)

_zeroconf_module: Any
try:
    import zeroconf as _zeroconf_module  # pyright: ignore[reportMissingImports]
except ImportError:
    _zeroconf_module = None

SERVICE_TYPE = "_expra-peer._tcp.local."
PROTOCOL_VERSION = "1"
SUPPORTED_PROTOCOL_VERSIONS = frozenset({"1"})
# mDNS SRV/TXT records have a protocol TTL of 4500 s (RFC 6762 §11.3).
# python-zeroconf's ServiceBrowser only fires update_service when a record
# CHANGES, not on routine re-announcements of the same data.  Setting this
# below the protocol TTL turns expire_stale() into a premature-eviction trap
# rather than a dead-peer safety net.  4800 s = 4500 s + 6.7 % margin so our
# safety net fires slightly after the protocol-level expiry, never before.
DEFAULT_TTL_SECONDS = 4800.0
REAP_TICK_SECONDS = 10.0

EventKind = str
EVENT_CANDIDATE = "candidate"
EVENT_LOST = "lost"


@dataclass(frozen=True, slots=True)
class DiscoveryEndpoint:
    """Endpoints this instance may advertise.

    ``port=None``/``connectable=False`` means no remote transport is listening
    yet: presence is advertised but no connection is claimed.
    """

    port: int | None = None
    connectable: bool = False


@dataclass(frozen=True, slots=True)
class DiscoveryAdvertisement:
    """The minimal, non-sensitive metadata this instance advertises."""

    stable_id: str
    display_name: str
    hostname: str
    app_version: str
    protocol_version: str = PROTOCOL_VERSION
    platform: str | None = None
    connectable: bool = False
    port: int | None = None
    identity_fingerprint: str | None = None
    transport_fingerprint: str | None = None
    root_public_key: str | None = None
    transport_generation: int | None = None
    transport_proof: str | None = None
    advertised_addresses: tuple[str, ...] | None = None


class DiscoveryBackend(Protocol):
    """Raw transport seam for discovery.

    ``start`` registers the service and begins browsing, delivering every
    transport event (``add``, ``update``, ``remove``) as
    ``listener(event, service_name, info)`` where ``info`` is a mapping-like
    object exposing ``properties``, ``addresses`` and ``port``. ``stop``
    unregisters and closes all sockets.
    """

    @property
    def available(self) -> bool: ...

    def start(self, advertisement: DiscoveryAdvertisement) -> None: ...

    def stop(self) -> None: ...


class ZeroconfDiscoveryBackend:
    """python-zeroconf implementation of the discovery backend.

    Falls back to ``available=False`` when the optional dependency is missing,
    so the rest of the app is unaffected. Service TXT properties are encoded
    as UTF-8 strings; the local service advertises only presence metadata.
    """

    def __init__(
        self,
        listener: Callable[[EventKind, str, Any], None],
        service_type: str = SERVICE_TYPE,
    ) -> None:
        self._listener = listener
        self._service_type = service_type
        self._zeroconf: Any = None
        self._service_info: Any = None
        self._browser: Any = None

    @property
    def available(self) -> bool:
        return _zeroconf_module is not None

    def start(self, advertisement: DiscoveryAdvertisement) -> None:
        if _zeroconf_module is None:
            raise RuntimeError("python-zeroconf is not installed")
        zeroconf_kwargs: dict[str, Any] = {}
        ip_version = getattr(_zeroconf_module, "IPVersion", None)
        all_versions = getattr(ip_version, "All", None)
        v4_only = getattr(ip_version, "V4Only", None)
        if all_versions is not None:
            zeroconf_kwargs["ip_version"] = all_versions
        try:
            self._start_with_zeroconf(advertisement, zeroconf_kwargs)
        except OSError:
            if all_versions is None or v4_only is None:
                raise
            LOGGER.warning(
                "All-interface Zeroconf startup failed; retrying with IPv4 only",
                exc_info=True,
            )
            self.stop()
            self._start_with_zeroconf(advertisement, {"ip_version": v4_only})

    def _start_with_zeroconf(
        self, advertisement: DiscoveryAdvertisement, zeroconf_kwargs: dict[str, Any]
    ) -> None:
        if _zeroconf_module is None:
            raise RuntimeError("python-zeroconf is not installed")
        zc = _zeroconf_module.Zeroconf(**zeroconf_kwargs)
        self._zeroconf = zc
        properties = {
            "id": advertisement.stable_id,
            "name": advertisement.display_name,
            "app_version": advertisement.app_version,
            "protocol_version": advertisement.protocol_version,
        }
        if advertisement.platform:
            properties["platform"] = advertisement.platform
        if advertisement.identity_fingerprint:
            properties["fingerprint"] = advertisement.identity_fingerprint
        if advertisement.transport_fingerprint:
            properties["tls_fingerprint"] = advertisement.transport_fingerprint
        if advertisement.root_public_key:
            properties["root_public_key"] = advertisement.root_public_key
        if advertisement.transport_generation is not None:
            properties["transport_generation"] = str(advertisement.transport_generation)
        if advertisement.transport_proof:
            properties["transport_proof"] = advertisement.transport_proof
        properties["connectable"] = "true" if advertisement.connectable else "false"
        port = advertisement.port or 0
        service_kwargs: dict[str, Any] = {
            "port": port,
            "properties": properties,
            "server": f"{advertisement.hostname}.local.",
        }
        addresses = _service_addresses(advertisement.advertised_addresses)
        if addresses:
            service_kwargs["addresses"] = addresses
        service_info = _zeroconf_module.ServiceInfo(
            self._service_type,
            f"{_service_instance_id(advertisement)}.{self._service_type}",
            **service_kwargs,
        )
        zc.register_service(service_info)
        listener = _ZeroconfListener(self._listener)
        browser = _zeroconf_module.ServiceBrowser(
            zc, self._service_type, cast(Any, listener)
        )
        self._service_info = service_info
        self._browser = browser

    def stop(self) -> None:
        browser = self._browser
        if browser is not None:
            try:
                browser.cancel()
            except Exception:
                LOGGER.debug("Failed to cancel zeroconf browser", exc_info=True)
            self._browser = None
        zc = self._zeroconf
        if zc is not None:
            try:
                zc.close()
            except Exception:
                LOGGER.debug("Failed to close zeroconf", exc_info=True)
            self._zeroconf = None


class _ZeroconfListener:
    def __init__(self, listener: Callable[[EventKind, str, Any], None]) -> None:
        self._listener = listener

    def add_service(self, zc: Any, type_: str, name: str) -> None:
        info = zc.get_service_info(type_, name)
        if info is not None:
            self._listener("add", name, info)

    def update_service(self, zc: Any, type_: str, name: str) -> None:
        info = zc.get_service_info(type_, name)
        if info is not None:
            self._listener("update", name, info)

    def remove_service(self, _zc: Any, _type_: str, name: str) -> None:
        self._listener("remove", name, None)


@dataclass
class _PeerRecord:
    candidate: DiscoveredNodeCandidate
    last_seen: float


class NetworkDiscovery:
    """Normalize, deduplicate, and track peers for one local-network service.

    The component is transport-agnostic: all raw transport events flow through
    the injected backend's listener callback. ``start``/``stop`` are
    idempotent; ``expire_stale`` must be ticked periodically by the coordinator
    so TTL expiry does not require unbounded background threads.
    """

    def __init__(
        self,
        local_node_id: Any,
        *,
        advertisement: DiscoveryAdvertisement,
        backend_factory: Callable[
            [Callable[[EventKind, str, Any], None]], DiscoveryBackend
        ]
        | None = None,
        service_type: str = SERVICE_TYPE,
        clock: Callable[[], float] = time.monotonic,
        ttl_seconds: float = DEFAULT_TTL_SECONDS,
        on_event: Callable[[EventKind, Any], None] | None = None,
    ) -> None:
        self._local_node_id = local_node_id
        self._advertisement = advertisement
        self._service_type = service_type
        self._clock = clock
        self._ttl_seconds = ttl_seconds
        self._on_event = on_event
        self._backend = (
            backend_factory(self._handle_transport_event)
            if backend_factory is not None
            else ZeroconfDiscoveryBackend(
                self._handle_transport_event, service_type=service_type
            )
        )
        self._peers: dict[str, _PeerRecord] = {}
        self._service_nodes: dict[str, str] = {}
        self._service_candidates: dict[str, DiscoveredNodeCandidate] = {}
        self._peer_lock = RLock()
        self._active = False
        self._unavailable_reason: str | None = None

    @property
    def available(self) -> bool:
        return self._backend.available

    @property
    def on_event(self) -> Callable[[EventKind, Any], None] | None:
        """Return the lifecycle bridge used by ``AppCoordinator``."""

        return self._on_event

    @on_event.setter
    def on_event(self, callback: Callable[[EventKind, Any], None] | None) -> None:
        self._on_event = callback

    @property
    def unavailable_reason(self) -> str | None:
        if self._unavailable_reason is not None:
            return self._unavailable_reason
        if not self._backend.available:
            return "python-zeroconf is not installed"
        return None

    def start(self) -> bool:
        with self._peer_lock:
            if self._active:
                return True
            if not self._backend.available:
                self._unavailable_reason = "python-zeroconf is not installed"
                LOGGER.info(
                    "Network discovery unavailable: %s", self._unavailable_reason
                )
                return False
            # Mark active before backend code so synchronous initial callbacks
            # are retained, then release the lock before backend callbacks.
            self._active = True
        try:
            self._backend.start(self._advertisement)
        except Exception as error:  # noqa: BLE001 - discovery must not fail the app.
            try:
                self._backend.stop()
            except Exception:
                LOGGER.debug(
                    "Discovery cleanup after failed start also failed", exc_info=True
                )
            with self._peer_lock:
                self._active = False
                self._peers.clear()
                self._service_nodes.clear()
                self._service_candidates.clear()
                self._unavailable_reason = f"Discovery start failed: {error}"
            LOGGER.warning("Network discovery failed to start: %s", error)
            return False
        with self._peer_lock:
            still_active = self._active
            if still_active:
                self._unavailable_reason = None
        if not still_active:
            try:
                self._backend.stop()
            except Exception:
                LOGGER.debug(
                    "Discovery cleanup after concurrent stop failed", exc_info=True
                )
            return False
        LOGGER.info("Network discovery active for %s", self._advertisement.stable_id)
        return True

    def stop(self) -> None:
        with self._peer_lock:
            if not self._active:
                return
            self._active = False
            try:
                self._backend.stop()
            except Exception as error:  # noqa: BLE001 - shutdown is best-effort.
                LOGGER.warning("Network discovery stop failed: %s", error)
            self._peers.clear()
            self._service_nodes.clear()
            self._service_candidates.clear()

    @property
    def active(self) -> bool:
        with self._peer_lock:
            return self._active

    def peers(self) -> tuple[DiscoveredNodeCandidate, ...]:
        with self._peer_lock:
            return tuple(record.candidate for record in self._peers.values())

    def _handle_transport_event(
        self, event: EventKind, service_name: str, info: Any
    ) -> None:
        emitted: list[tuple[EventKind, Any]] = []
        with self._peer_lock:
            self._handle_transport_event_locked(event, service_name, info, emitted)
        self._emit_events(emitted)

    def _handle_transport_event_locked(
        self,
        event: EventKind,
        service_name: str,
        info: Any,
        emitted: list[tuple[EventKind, Any]],
    ) -> None:
        if not self._active:
            return
        if event not in ("add", "update", "remove"):
            return
        try:
            if event == "remove":
                if not isinstance(service_name, str):
                    return
                node_id = self._service_nodes.pop(service_name, None)
                self._service_candidates.pop(service_name, None)
                if node_id is not None:
                    self._rebuild_peer(node_id, emitted)
                return
            candidate = self._normalize(service_name, info)
        except _MalformedAdvertisement as error:
            LOGGER.warning(
                "Ignoring malformed System Analyzer advertisement: %s", error
            )
            return
        if candidate is None:
            return
        previous_node_id = self._service_nodes.get(service_name)
        if previous_node_id is not None and previous_node_id != candidate.stable_id:
            self._service_candidates.pop(service_name, None)
            self._rebuild_peer(previous_node_id, emitted)
        self._service_nodes[service_name] = candidate.stable_id
        self._service_candidates[service_name] = candidate
        self._rebuild_peer(candidate.stable_id, emitted)

    def _rebuild_peer(self, node_id: str, emitted: list[tuple[EventKind, Any]]) -> None:
        observations = tuple(
            candidate
            for service_name, candidate in self._service_candidates.items()
            if self._service_nodes.get(service_name) == node_id
        )
        if not observations:
            self._drop_peer(node_id, emitted)
            return
        latest = max(observations, key=lambda item: item.last_seen)
        addresses = tuple(
            dict.fromkeys(
                address
                for observation in observations
                for address in observation.addresses
            )
        )
        endpoints = tuple(
            {
                endpoint.key: endpoint
                for observation in observations
                for endpoint in observation.endpoint_candidates
            }.values()
        )
        updated = replace(
            latest,
            addresses=addresses,
            endpoint_candidates=endpoints,
        )
        record = self._peers.get(node_id)
        if record is None:
            self._peers[node_id] = _PeerRecord(updated, latest.last_seen)
            emitted.append((EVENT_CANDIDATE, updated))
            return
        existing = record.candidate
        self._peers[node_id] = _PeerRecord(updated, latest.last_seen)
        changed = replace(existing, last_seen=updated.last_seen) != updated
        if changed:
            emitted.append((EVENT_CANDIDATE, updated))

    def _drop_peer(self, node_id: str, emitted: list[tuple[EventKind, Any]]) -> None:
        if node_id in self._peers:
            del self._peers[node_id]
            emitted.append((EVENT_LOST, node_id))

    def expire_stale(self, now: float | None = None) -> None:
        """Drop peers whose TTL has lapsed, emitting one lost event each.

        Called periodically by the coordinator; no background threads or timers
        live inside this component.
        """

        if now is None:
            now = self._clock()
        emitted: list[tuple[EventKind, Any]] = []
        with self._peer_lock:
            stale = [
                (node_id, record)
                for node_id, record in self._peers.items()
                if now - record.last_seen > self._ttl_seconds
            ]
            for node_id, record in stale:
                if node_id not in self._peers:
                    continue
                age = now - record.last_seen
                LOGGER.info(
                    "Dropping stale peer %s (last_seen %.0f s ago, ttl %.0f s)",
                    node_id,
                    age,
                    self._ttl_seconds,
                )
                self._drop_peer(node_id, emitted)
        self._emit_events(emitted)

    def _emit_events(self, events: list[tuple[EventKind, Any]]) -> None:
        for kind, payload in events:
            self._emit(kind, payload)

    def _emit(self, kind: EventKind, payload: Any) -> None:
        if self._on_event is not None:
            try:
                self._on_event(kind, payload)
            except Exception:
                LOGGER.debug("Discovery event callback failed", exc_info=True)

    def _node_id_for_service(self, service_name: str) -> str | None:
        if not service_name.endswith(self._service_type):
            return None
        node_id = service_name[: -len(self._service_type)]
        node_id = node_id.removesuffix(".")
        return node_id or None

    def _normalize(
        self, service_name: str, info: Any
    ) -> DiscoveredNodeCandidate | None:
        """Build a candidate from one raw service record, or None when self."""

        if not isinstance(service_name, str):
            raise _MalformedAdvertisement("service name must be a string")
        if self._node_id_for_service(service_name) is None:
            return None
        properties = _property_map(info)
        stable_id = properties.get("id")
        if not stable_id:
            # Zeroconf can deliver the service record before TXT properties;
            # wait for its next update instead of treating that transient state
            # as a malformed peer or surfacing a noisy warning for our service.
            return None
        local_node_id = getattr(self._local_node_id, "value", self._local_node_id)
        if stable_id == str(local_node_id):
            return None

        hostname = properties.get("name") or _service_hostname(
            service_name, self._service_type
        )
        app_version = properties.get("app_version", "")
        protocol_version = properties.get("protocol_version", "")
        platform = properties.get("platform") or None
        identity_fingerprint = properties.get("fingerprint") or None
        transport_fingerprint = properties.get("tls_fingerprint") or None
        root_public_key = properties.get("root_public_key") or None
        raw_generation = properties.get("transport_generation")
        transport_generation = (
            int(raw_generation)
            if raw_generation is not None and raw_generation.isdigit()
            else None
        )
        transport_proof = properties.get("transport_proof") or None
        connectable = properties.get("connectable", "false").casefold() == "true"

        port: int | None = None
        raw_port = _attr(info, "port")
        port = (
            raw_port
            if isinstance(raw_port, int)
            and not isinstance(raw_port, bool)
            and 1 <= raw_port <= 65535
            else None
        )

        addresses = tuple(_service_address_texts(info))

        return DiscoveredNodeCandidate(
            stable_id=stable_id,
            hostname=hostname,
            addresses=addresses,
            port=port,
            service_name=service_name,
            app_version=app_version,
            protocol_version=protocol_version,
            platform=platform,
            connectable=connectable and port is not None,
            compatible=protocol_version in SUPPORTED_PROTOCOL_VERSIONS,
            last_seen=self._clock(),
            identity_fingerprint=identity_fingerprint,
            transport_fingerprint=transport_fingerprint,
            root_public_key=root_public_key,
            transport_generation=transport_generation,
            transport_proof=transport_proof,
        )


class _MalformedAdvertisement(Exception):
    pass


def _local_service_addresses() -> list[bytes]:
    """Return usable local addresses for Zeroconf service resolution.

    A service with only an unresolved ``server`` hostname can be announced but
    cannot be resolved by another browser. Supplying concrete interface
    addresses lets Zeroconf resolve the TXT record and service metadata without
    assuming an Ethernet interface or a particular hostname.
    """

    if _zeroconf_module is None:
        return []
    addresses: list[bytes] = []
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", DeprecationWarning)
            ipv4_addresses = _zeroconf_module.get_all_addresses()
    except (AttributeError, OSError):
        ipv4_addresses = []
    for raw in ipv4_addresses:
        try:
            address = ipaddress.ip_address(raw)
            if address.is_loopback:
                continue
            addresses.append(socket.inet_pton(socket.AF_INET, str(address)))
        except (ValueError, OSError, TypeError):
            continue
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", DeprecationWarning)
            ipv6_addresses = _zeroconf_module.get_all_addresses_v6()
    except (AttributeError, OSError):
        ipv6_addresses = []
    for raw in ipv6_addresses:
        try:
            address_text = raw[0][0] if isinstance(raw[0], tuple) else raw[0]
            address = ipaddress.ip_address(address_text)
            if address.is_loopback:
                continue
            addresses.append(socket.inet_pton(socket.AF_INET6, str(address)))
        except (IndexError, ValueError, OSError, TypeError):
            continue
    return addresses


def _service_addresses(configured: tuple[str, ...] | None) -> list[bytes]:
    """Use an explicit address policy or discover all usable local addresses."""

    if configured is None:
        return _local_service_addresses()
    addresses: list[bytes] = []
    for raw in configured:
        try:
            address = ipaddress.ip_address(raw)
            if address.is_loopback:
                continue
            family = socket.AF_INET if address.version == 4 else socket.AF_INET6
            addresses.append(socket.inet_pton(family, str(address)))
        except (ValueError, OSError, TypeError):
            LOGGER.warning("Ignoring invalid configured discovery address %r", raw)
    return addresses


def _attr(info: Any, name: str) -> Any:
    if info is None:
        return None
    if isinstance(info, dict):
        return info.get(name)
    return getattr(info, name, None)


def _property_map(info: Any) -> dict[str, str]:
    raw = _attr(info, "properties")
    if not raw:
        return {}
    result: dict[str, str] = {}
    if isinstance(raw, Mapping):
        for key, value in raw.items():
            if key is None or value is None:
                continue
            result[_to_text(key)] = _to_text(value)
    return result


def _to_text(value: Any) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return str(value)


def _service_hostname(service_name: str, service_type: str = SERVICE_TYPE) -> str:
    if not service_name.endswith(service_type):
        return service_name
    node_id = service_name[: -len(service_type)].removesuffix(".")
    return node_id or service_name


def _service_instance_id(advertisement: DiscoveryAdvertisement) -> str:
    """Give a rotated transport a fresh mDNS instance for cache invalidation."""

    if (
        advertisement.transport_generation is not None
        and advertisement.transport_generation > 1
    ):
        return f"{advertisement.stable_id}-g{advertisement.transport_generation}"
    return advertisement.stable_id

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
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from threading import RLock
from typing import Any, Protocol, cast

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
        properties["connectable"] = "true" if advertisement.connectable else "false"
        port = advertisement.port or 0
        service_kwargs: dict[str, Any] = {
            "port": port,
            "properties": properties,
            "server": f"{advertisement.hostname}.local.",
        }
        addresses = _local_service_addresses()
        if addresses:
            service_kwargs["addresses"] = addresses
        service_info = _zeroconf_module.ServiceInfo(
            self._service_type,
            f"{advertisement.stable_id}.{self._service_type}",
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
            return self._start_locked()

    def _start_locked(self) -> bool:
        """Advertise this instance and begin browsing peers.

        Returns whether discovery became active. When the transport is
        unavailable the component records the reason and stays inactive without
        raising, so the application continues as a single-node app.
        """

        if self._active:
            return True
        if not self._backend.available:
            self._unavailable_reason = "python-zeroconf is not installed"
            LOGGER.info("Network discovery unavailable: %s", self._unavailable_reason)
            return False
        try:
            self._backend.start(self._advertisement)
        except Exception as error:  # noqa: BLE001 - discovery must not fail the app.
            try:
                self._backend.stop()
            except Exception:
                LOGGER.debug(
                    "Discovery cleanup after failed start also failed", exc_info=True
                )
            self._peers.clear()
            self._service_nodes.clear()
            self._unavailable_reason = f"Discovery start failed: {error}"
            LOGGER.warning("Network discovery failed to start: %s", error)
            return False
        self._active = True
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
        with self._peer_lock:
            self._handle_transport_event_locked(event, service_name, info)

    def _handle_transport_event_locked(
        self, event: EventKind, service_name: str, info: Any
    ) -> None:
        if not self._active:
            return
        try:
            if event == "remove":
                node_id = self._service_nodes.pop(service_name, None)
                if node_id is not None:
                    self._drop_peer(node_id)
                return
            candidate = self._normalize(service_name, info)
        except _MalformedAdvertisement as error:
            LOGGER.warning(
                "Ignoring malformed System Analyzer advertisement: %s", error
            )
            return
        if candidate is None:
            return
        self._service_nodes[service_name] = candidate.stable_id
        record = self._peers.get(candidate.stable_id)
        if record is not None:
            existing = record.candidate
            updated = DiscoveredNodeCandidate(
                stable_id=candidate.stable_id,
                hostname=candidate.hostname,
                addresses=candidate.addresses,
                port=candidate.port,
                service_name=candidate.service_name,
                app_version=candidate.app_version,
                protocol_version=candidate.protocol_version,
                platform=candidate.platform,
                connectable=candidate.connectable,
                compatible=candidate.compatible,
                last_seen=candidate.last_seen,
                identity_fingerprint=candidate.identity_fingerprint,
                transport_fingerprint=candidate.transport_fingerprint,
            )
            changed = (
                existing.addresses != updated.addresses
                or existing.hostname != updated.hostname
                or existing.app_version != updated.app_version
                or existing.port != updated.port
                or existing.connectable != updated.connectable
                or existing.identity_fingerprint != updated.identity_fingerprint
                or existing.transport_fingerprint != updated.transport_fingerprint
            )
            self._peers[candidate.stable_id] = _PeerRecord(updated, candidate.last_seen)
            if changed:
                self._emit(EVENT_CANDIDATE, updated)
            return
        self._peers[candidate.stable_id] = _PeerRecord(candidate, candidate.last_seen)
        self._emit(EVENT_CANDIDATE, candidate)

    def _drop_peer(self, node_id: str) -> None:
        with self._peer_lock:
            if node_id in self._peers:
                del self._peers[node_id]
                self._emit(EVENT_LOST, node_id)

    def expire_stale(self, now: float | None = None) -> None:
        """Drop peers whose TTL has lapsed, emitting one lost event each.

        Called periodically by the coordinator; no background threads or timers
        live inside this component.
        """

        if now is None:
            now = self._clock()
        with self._peer_lock:
            stale = [
                node_id
                for node_id, record in self._peers.items()
                if now - record.last_seen > self._ttl_seconds
            ]
            for node_id in stale:
                age = now - self._peers[node_id].last_seen
                LOGGER.info(
                    "Dropping stale peer %s (last_seen %.0f s ago, ttl %.0f s)",
                    node_id,
                    age,
                    self._ttl_seconds,
                )
                self._drop_peer(node_id)

    def _emit(self, kind: EventKind, payload: Any) -> None:
        if self._on_event is not None:
            self._on_event(kind, payload)

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
        connectable = properties.get("connectable", "false").casefold() == "true"

        port: int | None = None
        raw_port = _attr(info, "port")
        port = (
            raw_port
            if isinstance(raw_port, int)
            and not isinstance(raw_port, bool)
            and 0 <= raw_port <= 65535
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
            connectable=connectable,
            compatible=protocol_version in SUPPORTED_PROTOCOL_VERSIONS,
            last_seen=self._clock(),
            identity_fingerprint=identity_fingerprint,
            transport_fingerprint=transport_fingerprint,
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
    if isinstance(raw, dict):
        for key, value in raw.items():
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


def _address_texts(addresses: Any) -> list[str]:
    if not addresses:
        return []
    texts: list[str] = []
    for address in addresses:
        if isinstance(address, bytes):
            try:
                family = socket.AF_INET if len(address) == 4 else socket.AF_INET6
                texts.append(socket.inet_ntop(family, address))
                continue
            except Exception:
                LOGGER.debug("Failed to convert address %r", address, exc_info=True)
        texts.append(str(address))
    return texts


def _service_address_texts(info: Any) -> list[str]:
    """Read all resolved service addresses across Zeroconf IP versions."""

    for method_name in ("parsed_scoped_addresses", "parsed_addresses"):
        method = _attr(info, method_name)
        if callable(method):
            try:
                parsed = method()
            except Exception:
                LOGGER.debug("Failed to parse service addresses", exc_info=True)
            else:
                if parsed:
                    return [str(address) for address in cast(Iterable[Any], parsed)]
    return _address_texts(_attr(info, "addresses"))

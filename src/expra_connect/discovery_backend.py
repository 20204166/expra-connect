"""Optional Zeroconf adapter; discovery remains usable without Avahi.

This module is the small OS/network boundary between python-zeroconf and the
discovery layer above it. It owns exactly: starting Zeroconf, listening for
add/update/remove, resolving ``ServiceInfo``, decoding raw addresses into text,
and cleanly releasing Zeroconf resources. It never owns candidate maps, peer
TTL, identity deduplication, self filtering, trust, route ranking, reconnect,
or discovery lifecycle generations -- those stay with ``NetworkDiscovery`` and
the runtime.
"""

from __future__ import annotations

import logging
import socket
from collections.abc import Callable, Iterable
from typing import Any, cast

LOGGER = logging.getLogger(__name__)

_zeroconf_module: Any
try:
    import zeroconf as _zeroconf_module  # pyright: ignore[reportMissingImports]
except ImportError:
    _zeroconf_module = None

_PARSED_ADDRESS_METHODS = ("parsed_scoped_addresses", "parsed_addresses")


class ZeroconfBackend:
    """Browse-only python-zeroconf adapter.

    ``start`` is transactional and idempotent; ``stop`` is idempotent and
    best-effort. Each ``start`` binds its listener to a fresh generation token
    so a delayed callback from a previous generation cannot become a current
    observation, without building a second lifecycle framework above the
    adapter.
    """

    def __init__(
        self, service_type: str, on_event: Callable[[str, dict[str, Any]], None]
    ) -> None:
        self.service_type = service_type
        self._on_event = on_event
        self._zeroconf: Any = None
        self._browser: Any = None
        self._token: object | None = None

    def start(self) -> None:
        if self._token is not None:
            return
        if _zeroconf_module is None:
            raise RuntimeError("zeroconf discovery is unavailable")

        token = object()
        self._token = token
        backend = self

        class Listener:
            def add_service(self, zc: Any, service_type: str, name: str) -> None:
                if backend._token is token:
                    backend._emit(zc, service_type, name, "add")

            def update_service(self, zc: Any, service_type: str, name: str) -> None:
                if backend._token is token:
                    backend._emit(zc, service_type, name, "update")

            def remove_service(self, _zc: Any, _service_type: str, name: str) -> None:
                if backend._token is token and isinstance(name, str):
                    backend._emit_remove(name)

        zeroconf: Any = None
        try:
            zeroconf = _zeroconf_module.Zeroconf()
            browser = _zeroconf_module.ServiceBrowser(
                zeroconf, self.service_type, cast(Any, Listener())
            )
        except Exception:
            self._token = None
            self._zeroconf = None
            self._browser = None
            if zeroconf is not None:
                try:
                    zeroconf.close()
                except Exception:
                    LOGGER.debug(
                        "Failed to close zeroconf after a failed start", exc_info=True
                    )
            raise
        self._zeroconf = zeroconf
        self._browser = browser

    def _emit(self, zc: Any, service_type: str, name: str, event: str) -> None:
        try:
            info = zc.get_service_info(service_type, name)
        except Exception:
            LOGGER.debug("Failed to resolve zeroconf service %r", name, exc_info=True)
            return
        if info is None:
            return
        self._deliver(
            event,
            {
                "name": name,
                "addresses": tuple(_service_address_texts(info)),
                "port": _attr(info, "port"),
            },
        )

    def _emit_remove(self, name: str) -> None:
        self._deliver("remove", {"name": name})

    def _deliver(self, event: str, payload: dict[str, Any]) -> None:
        try:
            self._on_event(event, payload)
        except Exception:
            LOGGER.debug(
                "Zeroconf discovery callback failed for %r", event, exc_info=True
            )

    def stop(self) -> None:
        self._token = None
        browser, zeroconf = self._browser, self._zeroconf
        self._browser = None
        self._zeroconf = None
        if browser is not None:
            try:
                browser.cancel()
            except Exception:
                LOGGER.debug("Failed to cancel zeroconf browser", exc_info=True)
        if zeroconf is not None:
            try:
                zeroconf.close()
            except Exception:
                LOGGER.debug("Failed to close zeroconf", exc_info=True)


def _address_text(address: Any) -> str | None:
    """Decode one observed address into text, or ``None`` when unusable.

    Only 4-byte IPv4 and 16-byte IPv6 packed forms and textual addresses are
    accepted. Malformed packed lengths and arbitrary objects are skipped rather
    than stringified into a bogus endpoint.
    """

    if isinstance(address, str):
        return address.strip() or None
    if not isinstance(address, bytes):
        return None
    if len(address) == 4:
        family = socket.AF_INET
    elif len(address) == 16:
        family = socket.AF_INET6
    else:
        return None
    try:
        return socket.inet_ntop(family, address)
    except OSError:
        return None


def _address_texts(addresses: Any) -> list[str]:
    """Decode an address collection, preserving first-observed order."""

    if not addresses:
        return []
    candidates: Iterable[Any]
    if isinstance(addresses, (str, bytes)):
        candidates = (addresses,)
    else:
        try:
            candidates = iter(addresses)
        except TypeError:
            return []
    texts: list[str] = []
    seen: set[str] = set()
    for address in candidates:
        text = _address_text(address)
        if text is not None and text not in seen:
            seen.add(text)
            texts.append(text)
    return texts


def _service_address_texts(info: Any) -> list[str]:
    """Read all resolved service addresses across Zeroconf IP versions.

    Modern APIs (``parsed_scoped_addresses`` then ``parsed_addresses``) are
    preferred so IPv6 and link-local scope survive; the raw ``addresses``
    property is IPv4-only and is used only as a feature-detected fallback.
    """

    for method_name in _PARSED_ADDRESS_METHODS:
        method = _attr(info, method_name)
        if callable(method):
            try:
                parsed = method()
            except Exception:
                LOGGER.debug("Failed to parse service addresses", exc_info=True)
            else:
                if parsed:
                    try:
                        return _address_texts(cast(Iterable[Any], parsed))
                    except TypeError:
                        LOGGER.debug(
                            "Parsed service addresses are not iterable", exc_info=True
                        )
    return _address_texts(_attr(info, "addresses"))


def _attr(info: Any, name: str) -> Any:
    if info is None:
        return None
    if isinstance(info, dict):
        return info.get(name)
    return getattr(info, name, None)

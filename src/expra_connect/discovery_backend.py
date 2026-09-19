"""Optional Zeroconf adapter; discovery remains usable without Avahi."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, cast


class ZeroconfBackend:
    def __init__(
        self, service_type: str, on_event: Callable[[str, dict[str, Any]], None]
    ) -> None:
        self.service_type = service_type
        self._on_event = on_event
        self._zeroconf: Any = None
        self._browser: Any = None

    def start(self) -> None:
        try:
            from zeroconf import ServiceBrowser, Zeroconf
        except ImportError as error:
            raise RuntimeError("zeroconf discovery is unavailable") from error
        self._zeroconf = Zeroconf()

        backend = self

        class Listener:
            def add_service(self, zc: Any, service_type: str, name: str) -> None:
                backend._emit(zc, service_type, name, "add")

            def update_service(self, zc: Any, service_type: str, name: str) -> None:
                backend._emit(zc, service_type, name, "update")

            def remove_service(self, _zc: Any, _service_type: str, name: str) -> None:
                backend._on_event("remove", {"name": name})

        self._browser = ServiceBrowser(
            self._zeroconf, self.service_type, cast(Any, Listener())
        )

    def _emit(self, zc: Any, service_type: str, name: str, event: str) -> None:
        info = zc.get_service_info(service_type, name)
        if info is None:
            return
        addresses = tuple(
            address.decode() if isinstance(address, bytes) else address
            for address in info.addresses
        )
        self._on_event(event, {"name": name, "addresses": addresses, "port": info.port})

    def stop(self) -> None:
        if self._browser is not None:
            self._browser.cancel()
            self._browser = None
        if self._zeroconf is not None:
            self._zeroconf.close()
            self._zeroconf = None

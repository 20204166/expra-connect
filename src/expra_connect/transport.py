"""Neutral dictionary adapter over the extracted mature socket transport."""

from __future__ import annotations

import json
from threading import Event
from typing import Any, cast

from .socket_transport import SocketRemoteTransport
from .wire_protocol import RemoteTransportError

TransportError = RemoteTransportError


class SocketTransport:
    """Expose JSON objects while preserving the mature deadline semantics."""

    def __init__(self, host: str, port: int, *, timeout: float = 5.0) -> None:
        self._transport = SocketRemoteTransport(host, port, timeout=timeout)

    def request(
        self, payload: dict[str, Any], *, cancel_event: Event | None = None
    ) -> dict[str, Any]:
        response = self._transport.request(json.dumps(payload), cancel_event)
        return cast(dict[str, Any], json.loads(response))

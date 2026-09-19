"""Public protocol surface backed by the extracted mature wire contract."""

from typing import Any

from .socket_transport import _recv_frame, _send_frame
from .wire_protocol import *
from .wire_protocol import MAX_ENVELOPE_BYTES

MAX_FRAME = MAX_ENVELOPE_BYTES


def encode_frame(sock: Any, payload: bytes, *, max_bytes: int = MAX_FRAME) -> None:
    _send_frame(sock, payload, max_bytes=max_bytes)


def decode_frame(
    sock: Any,
    *,
    max_bytes: int = MAX_FRAME,
    closed_message: str = "connection closed before response",
) -> bytes:
    return _recv_frame(sock, max_bytes=max_bytes, closed_message=closed_message)

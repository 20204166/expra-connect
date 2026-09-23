"""Remote transport mechanics: in-process and socket transports.

``SocketRemoteTransport`` is the length-prefixed JSON client for one TCP
request/response exchange (TLS with optional certificate pinning);
``MemoryRemoteTransport`` hands envelopes straight to a ``RemoteService`` for
in-process/tests; ``build_trusted_transport`` derives a pinned client from a
persisted trusted-node record. Frame helpers are shared with the listening
server.
"""

import hmac
import inspect
import math
import socket as socket_module
import ssl
import struct
import time
from collections.abc import Callable
from contextlib import suppress
from typing import Any

from .models import TrustedNodeRecord
from .tls_material import certificate_fingerprint
from .wire_protocol import (
    MAX_ENVELOPE_BYTES,
    RemoteAuthError,
    RemoteExecutionError,
    RemoteProtocolError,
    RemoteTransportError,
)

__all__ = [
    "MemoryRemoteTransport",
    "RemoteTransportError",
    "SocketRemoteTransport",
    "TLSRemoteTransport",
    "build_trusted_transport",
    "request_with_retry",
]

CANCEL_POLL_INTERVAL = 0.25


def _validate_max_bytes(max_bytes: int) -> None:
    if isinstance(max_bytes, bool) or not isinstance(max_bytes, int):
        raise TypeError("max_bytes must be an integer")
    if max_bytes < 0:
        raise ValueError("max_bytes must not be negative")


def _validate_endpoint(host: str, port: int, timeout: float) -> None:
    if not isinstance(host, str):
        raise TypeError("host must be text")
    if not host.strip():
        raise ValueError("host must not be empty")
    if isinstance(port, bool) or not isinstance(port, int):
        raise TypeError("port must be an integer")
    if not 1 <= port <= 65535:
        raise ValueError("port must be between 1 and 65535")
    if isinstance(timeout, bool) or not isinstance(timeout, (int, float)):
        raise TypeError("timeout must be a real number")
    if not math.isfinite(timeout) or timeout <= 0:
        raise ValueError("timeout must be finite and positive")


def _validate_fingerprint(expected_fingerprint: str | None) -> None:
    if expected_fingerprint is None:
        return
    if not isinstance(expected_fingerprint, str):
        raise TypeError("expected_fingerprint must be text")
    if not expected_fingerprint.strip():
        raise ValueError("expected_fingerprint must not be empty")


def _check_cancelled(cancel_event: Any | None) -> None:
    if cancel_event is not None and cancel_event.is_set():
        raise RemoteExecutionError("cancelled")


def _remaining(deadline: float) -> float:
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise TimeoutError("operation timed out")
    return remaining


class MemoryRemoteTransport:
    """In-process transport that hands envelopes straight to a ``RemoteService``.

    Used by tests and by any in-app loopback path; the authentication and
    authorization checks run exactly as they would over a socket.
    """

    def __init__(self, service: Any) -> None:
        self._service = service

    def request(self, envelope_text: str, cancel_event: Any | None = None) -> str:
        _check_cancelled(cancel_event)
        response = self._service.handle(envelope_text)
        if not isinstance(response, str):
            raise RemoteProtocolError("service response must be text")
        return response


def request_with_retry(
    request: Callable[..., str],
    envelope_text: str,
    cancel_event: Any | None,
    attempts: int,
) -> str:
    """Invoke a transport while retaining one envelope for safe retries."""

    if isinstance(attempts, bool) or not isinstance(attempts, int):
        raise TypeError("attempts must be an integer")
    if attempts < 1:
        raise ValueError("attempts must be positive")
    for attempt in range(attempts):
        _check_cancelled(cancel_event)
        try:
            return _invoke_with_optional_cancel(request, envelope_text, cancel_event)
        except RemoteTransportError:
            if attempt + 1 == attempts:
                raise
    raise RemoteTransportError("remote transport returned no response")


def _invoke_with_optional_cancel(
    request: Callable[..., str], envelope_text: str, cancel_event: Any | None
) -> str:
    """Support transport callbacks with or without cancellation support."""

    try:
        signature = inspect.signature(request)
    except (TypeError, ValueError):
        return request(envelope_text)
    try:
        signature.bind(envelope_text, cancel_event)
    except TypeError:
        try:
            signature.bind(envelope_text, cancel_event=cancel_event)
        except TypeError:
            return request(envelope_text)
        return request(envelope_text, cancel_event=cancel_event)
    return request(envelope_text, cancel_event)


def _recv_exact(
    sock: Any,
    length: int,
    *,
    closed_message: str = "connection closed before response",
    cancel_event: Any | None = None,
    deadline: float | None = None,
) -> bytes:
    if isinstance(length, bool) or not isinstance(length, int):
        raise TypeError("length must be an integer")
    if length < 0:
        raise ValueError("length must not be negative")
    chunks: list[bytes] = []
    remaining = length
    while remaining:
        _check_cancelled(cancel_event)
        timeout: float | None = None
        if deadline is not None:
            timeout = _remaining(deadline)
            if cancel_event is not None:
                timeout = min(CANCEL_POLL_INTERVAL, timeout)
        elif cancel_event is not None:
            timeout = CANCEL_POLL_INTERVAL
        if timeout is not None:
            settimeout = getattr(sock, "settimeout", None)
            if callable(settimeout):
                settimeout(timeout)
        try:
            chunk = sock.recv(remaining)
        except TimeoutError:
            if cancel_event is None:
                raise
            continue
        if not chunk:
            raise RemoteTransportError(closed_message)
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


def _recv_frame(
    sock: Any,
    *,
    max_bytes: int,
    closed_message: str,
    cancel_event: Any | None = None,
    deadline: float | None = None,
) -> bytes:
    _validate_max_bytes(max_bytes)
    header = _recv_exact(
        sock,
        4,
        closed_message=closed_message,
        cancel_event=cancel_event,
        deadline=deadline,
    )
    length = struct.unpack(">I", header)[0]
    if length == 0:
        raise RemoteProtocolError("empty frame is not a valid envelope")
    if length > max_bytes:
        raise RemoteTransportError("envelope is too large")
    return _recv_exact(
        sock,
        length,
        closed_message=closed_message,
        cancel_event=cancel_event,
        deadline=deadline,
    )


def _send_frame(sock: Any, payload: bytes, *, max_bytes: int) -> None:
    _validate_max_bytes(max_bytes)
    if not isinstance(payload, bytes):
        raise TypeError("payload must be bytes")
    if not payload:
        raise RemoteProtocolError("empty frame is not a valid envelope")
    if len(payload) > max_bytes:
        raise RemoteTransportError("envelope is too large")
    sock.sendall(struct.pack(">I", len(payload)) + payload)


class SocketRemoteTransport:
    """Length-prefixed JSON client for one TCP request/response exchange."""

    def __init__(
        self,
        host: str,
        port: int,
        *,
        timeout: float = 10.0,
        ssl_context: ssl.SSLContext | None = None,
        expected_fingerprint: str | None = None,
    ) -> None:
        _validate_endpoint(host, port, timeout)
        _validate_fingerprint(expected_fingerprint)
        if expected_fingerprint is not None and ssl_context is None:
            raise ValueError("certificate fingerprint requires TLS")
        if (
            ssl_context is not None
            and ssl_context.verify_mode == ssl.CERT_NONE
            and expected_fingerprint is None
        ):
            raise ValueError("CERT_NONE TLS requires a certificate fingerprint")
        self._host = host
        self._port = port
        self._timeout = timeout
        self._ssl_context = ssl_context
        self._expected_fingerprint = expected_fingerprint

    def request(self, envelope_text: str, cancel_event: Any | None = None) -> str:
        deadline = time.monotonic() + self._timeout
        if not isinstance(envelope_text, str):
            raise TypeError("envelope_text must be text")
        data = envelope_text.encode("utf-8")
        if len(data) > MAX_ENVELOPE_BYTES:
            raise RemoteTransportError("request envelope is too large")
        wrapped_socket: Any | None = None
        try:
            _check_cancelled(cancel_event)
            with socket_module.create_connection(
                (self._host, self._port), timeout=_remaining(deadline)
            ) as sock:
                _check_cancelled(cancel_event)
                sock.settimeout(_remaining(deadline))
                if self._ssl_context is not None:
                    sock.settimeout(_remaining(deadline))
                    _check_cancelled(cancel_event)
                    sock = self._ssl_context.wrap_socket(
                        sock, server_hostname=self._host
                    )
                    wrapped_socket = sock
                    _check_cancelled(cancel_event)
                    certificate = sock.getpeercert(binary_form=True)
                    if certificate is None:
                        raise RemoteAuthError("peer certificate is missing")
                    fingerprint = certificate_fingerprint(certificate)
                    if (
                        self._expected_fingerprint is not None
                        and not hmac.compare_digest(
                            fingerprint, self._expected_fingerprint
                        )
                    ):
                        raise RemoteAuthError("peer certificate fingerprint changed")
                _check_cancelled(cancel_event)
                sock.settimeout(_remaining(deadline))
                _send_frame(sock, data, max_bytes=MAX_ENVELOPE_BYTES)
                _check_cancelled(cancel_event)
                body = _recv_frame(
                    sock,
                    max_bytes=MAX_ENVELOPE_BYTES,
                    closed_message="connection closed before response",
                    cancel_event=cancel_event,
                    deadline=deadline,
                )
        except ssl.SSLCertVerificationError as error:
            raise RemoteAuthError("peer certificate verification failed") from error
        except TimeoutError as error:
            if cancel_event is not None and cancel_event.is_set():
                raise RemoteExecutionError("cancelled") from error
            raise RemoteTransportError("remote transport timed out") from error
        except RemoteTransportError as error:
            if cancel_event is not None and cancel_event.is_set():
                raise RemoteExecutionError("cancelled") from error
            raise
        except RemoteAuthError:
            raise
        except OSError as error:
            if cancel_event is not None and cancel_event.is_set():
                raise RemoteExecutionError("cancelled") from error
            raise RemoteTransportError(
                f"remote transport failed: {type(error).__name__}: {error}"
            ) from error
        finally:
            if wrapped_socket is not None:
                with suppress(OSError):
                    wrapped_socket.close()
        try:
            return body.decode("utf-8")
        except UnicodeDecodeError as error:
            raise RemoteProtocolError("response is not valid UTF-8") from error


class TLSRemoteTransport(SocketRemoteTransport):
    """Socket transport with TLS encryption and required certificate pinning."""

    def __init__(
        self,
        host: str,
        port: int,
        *,
        expected_fingerprint: str,
        timeout: float = 10.0,
    ) -> None:
        from .tls_material import client_context

        super().__init__(
            host,
            port,
            timeout=timeout,
            ssl_context=client_context(),
            expected_fingerprint=expected_fingerprint,
        )


def build_trusted_transport(
    record: TrustedNodeRecord,
    *,
    transport_cls: Callable[..., Any] = TLSRemoteTransport,
) -> Any:
    """Build a pinned transport from persisted trusted-node data."""

    if not isinstance(record.host, str) or not record.host.strip():
        raise RemoteAuthError("trusted peer has no complete endpoint")
    if (
        isinstance(record.port, bool)
        or not isinstance(record.port, int)
        or not 1 <= record.port <= 65535
    ):
        raise RemoteAuthError("trusted peer has an invalid endpoint")
    if (
        not isinstance(record.transport_fingerprint, str)
        or not record.transport_fingerprint.strip()
    ):
        raise RemoteAuthError("trusted peer has no pinned TLS fingerprint")
    return transport_cls(
        record.host,
        record.port,
        expected_fingerprint=record.transport_fingerprint,
    )

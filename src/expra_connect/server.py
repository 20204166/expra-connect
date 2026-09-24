"""Bounded headless peer listener for the extracted wire contract."""

from __future__ import annotations

import inspect
import json
import logging
import secrets
import socketserver
import ssl
import threading
from collections.abc import Callable
from typing import Any

from .identity import NodeId
from .models import NodePermission
from .protocol import MAX_FRAME, decode_frame, encode_frame
from .socket_transport import _recv_frame, _send_frame
from .wire_protocol import (
    DEFAULT_MAX_ACTIVE_HANDLERS,
    MAX_ENVELOPE_BYTES,
    PAIRING_MODE_TRANSACTIONAL,
    CapabilityElevationRequest,
    PairingControlRequest,
    PairingRequest,
    RemoteProtocolError,
    validate_pairing_control_request,
)

LOGGER = logging.getLogger(__name__)
PEER_SERVICE_DEFAULT_PORT = 27321


class PeerServer:
    """Compatibility dictionary server used by small local integrations."""

    def __init__(
        self, host: str, port: int, handler: Callable[[dict[str, Any]], dict[str, Any]]
    ) -> None:
        self._handler = handler
        callback = handler

        class Request(socketserver.BaseRequestHandler):
            def handle(self) -> None:
                request = json.loads(
                    decode_frame(self.request, max_bytes=MAX_FRAME).decode()
                )
                encode_frame(
                    self.request,
                    json.dumps(callback(request)).encode(),
                    max_bytes=MAX_FRAME,
                )

        self._server = socketserver.ThreadingTCPServer(
            (host, port), Request, bind_and_activate=False
        )
        self._server.daemon_threads = True
        self._server.allow_reuse_address = True

    @property
    def address(self) -> tuple[str, int]:
        host, port = self._server.server_address[:2]
        return str(host), int(port)

    def start(self) -> None:
        self._server.server_bind()
        self._server.server_activate()
        threading.Thread(target=self._server.serve_forever, daemon=True).start()

    def stop(self) -> None:
        self._server.shutdown()
        self._server.server_close()


class RemoteSocketServer:
    """Bounded framed listener that delegates authenticated work to a service."""

    def __init__(
        self,
        service: Any,
        *,
        host: str = "127.0.0.1",
        port: int = 0,
        preferred_port: int = 0,
        timeout: float = 10.0,
        max_active_handlers: int = DEFAULT_MAX_ACTIVE_HANDLERS,
        ssl_context: ssl.SSLContext | None = None,
        pairing_handler: Callable[[PairingRequest], Any] | None = None,
        pair_confirm_handler: Callable[[PairingControlRequest], bool] | None = None,
        pair_abort_handler: Callable[[PairingControlRequest], bool] | None = None,
        elevation_handler: Callable[[CapabilityElevationRequest], bool] | None = None,
    ) -> None:
        if max_active_handlers < 1:
            raise ValueError("max_active_handlers must be positive")
        self._service = service
        self._host = host
        self._port = port
        self._preferred_port = preferred_port
        self._timeout = timeout
        self._max_active_handlers = max_active_handlers
        self._ssl_context = ssl_context
        self._pairing_handler = pairing_handler
        self._pair_confirm_handler = pair_confirm_handler
        self._pair_abort_handler = pair_abort_handler
        self._elevation_handler = elevation_handler
        self._server: Any = None
        self._thread: threading.Thread | None = None
        self._admission: threading.BoundedSemaphore | None = None
        self._preferred_port_honored: bool | None = None

    @property
    def bound_port(self) -> int | None:
        return None if self._server is None else int(self._server.server_address[1])

    @property
    def preferred_port_honored(self) -> bool | None:
        return self._preferred_port_honored

    def start(self) -> None:
        if self._server is not None:
            return

        service = self._service
        timeout = self._timeout
        ssl_context = self._ssl_context
        pairing_handler = self._pairing_handler
        pair_confirm_handler = self._pair_confirm_handler
        pair_abort_handler = self._pair_abort_handler
        elevation_handler = self._elevation_handler
        admission = threading.BoundedSemaphore(self._max_active_handlers)
        self._admission = admission

        class Handler(socketserver.BaseRequestHandler):
            def handle(self) -> None:
                request_socket: Any = self.request
                try:
                    request_socket.settimeout(timeout)
                    if ssl_context is not None:
                        request_socket = ssl_context.wrap_socket(
                            self.request, server_side=True
                        )
                    body = _recv_frame(
                        request_socket,
                        max_bytes=MAX_ENVELOPE_BYTES,
                        closed_message="connection closed before request",
                    )
                    text = body.decode("utf-8")
                    raw = json.loads(text)
                    if isinstance(raw, dict) and raw.get("op") == "pair_request":
                        response = _pair_request_response(raw, pairing_handler)
                    elif isinstance(raw, dict) and raw.get("op") in {
                        "pair_confirm",
                        "pair_abort",
                    }:
                        handler = (
                            pair_confirm_handler
                            if raw["op"] == "pair_confirm"
                            else pair_abort_handler
                        )
                        response = _pair_control_response(raw, handler)
                    elif isinstance(raw, dict) and raw.get("op") == "elevation_request":
                        response = _elevation_response(raw, elevation_handler)
                    else:
                        generation = secrets.token_hex(16)
                        handle = service.handle
                        try:
                            inspect.signature(handle).bind(
                                text, connection_generation=generation
                            )
                        except (TypeError, ValueError):
                            response = handle(text)
                        else:
                            response = handle(text, connection_generation=generation)
                    if not isinstance(response, str):
                        raise RemoteProtocolError("service response must be text")
                    _send_frame(
                        request_socket,
                        response.encode("utf-8"),
                        max_bytes=MAX_ENVELOPE_BYTES,
                    )
                except Exception:  # Auth/protocol failures close silently.
                    LOGGER.debug("peer request rejected", exc_info=True)
                finally:
                    if request_socket is not self.request:
                        request_socket.close()
                    admission.release()

        class Server(socketserver.ThreadingTCPServer):
            allow_reuse_address = True
            request_queue_size = 16

            def process_request(self, request: Any, client_address: Any) -> None:
                if not admission.acquire(blocking=False):
                    request.close()
                    return
                try:
                    super().process_request(request, client_address)
                except BaseException:
                    admission.release()
                    request.close()
                    raise

        candidates = (
            [self._preferred_port, self._port]
            if self._preferred_port > 0
            else [self._port]
        )
        for candidate in candidates:
            try:
                server = Server((self._host, candidate), Handler)
                self._preferred_port_honored = (
                    candidate == self._preferred_port
                    if self._preferred_port > 0
                    else None
                )
                break
            except OSError:
                if candidate == self._port or self._preferred_port == 0:
                    raise
        else:
            raise OSError("could not bind peer listener")
        server.daemon_threads = True
        server.block_on_close = False
        self._server = server
        self._thread = threading.Thread(target=server.serve_forever, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        server = self._server
        self._server = None
        self._thread = None
        if server is not None:
            try:
                server.shutdown()
            except Exception:  # shutdown is best-effort.
                LOGGER.debug("peer socket server shutdown failed", exc_info=True)
            server.server_close()

    def update_grants(self, grants: dict[NodeId, Any]) -> None:
        updater = getattr(self._service, "update_grants", None)
        if updater is None:
            raise AttributeError("service does not support grant updates")
        updater(grants)

    def update_cluster_fence(
        self, *, cluster_id: str, coordinator_epoch: int, fencing_token: str
    ) -> None:
        updater = getattr(self._service, "update_cluster_fence", None)
        if updater is None:
            raise AttributeError("service does not support cluster fencing")
        updater(
            cluster_id=cluster_id,
            coordinator_epoch=coordinator_epoch,
            fencing_token=fencing_token,
        )


def _pair_request_response(
    raw: dict[str, Any], handler: Callable[[PairingRequest], Any] | None
) -> str:
    required = {
        "op",
        "pairing_mode",
        "caller_node_id",
        "identity_fingerprint",
        "transport_fingerprint",
        "secret",
        "permissions",
    }
    allowed = required | {
        "root_public_key",
        "transport_generation",
        "transport_proof",
        "intent",
    }
    if (
        handler is None
        or not required <= set(raw)
        or not set(raw) <= allowed
        or raw.get("pairing_mode") != PAIRING_MODE_TRANSACTIONAL
    ):
        return json.dumps({"approved": False, "error": "pairing_unavailable"})
    permissions = raw["permissions"]
    if not isinstance(permissions, list) or any(
        not isinstance(item, str) for item in permissions
    ):
        return json.dumps({"approved": False, "error": "invalid_pairing"})
    intent = raw.get("intent", "pair")
    if not isinstance(intent, str) or intent not in {"pair", "repair"}:
        return json.dumps({"approved": False, "error": "invalid_pairing"})
    try:
        request = PairingRequest(
            caller_node_id=NodeId(raw["caller_node_id"]),
            identity_fingerprint=raw["identity_fingerprint"],
            transport_fingerprint=raw["transport_fingerprint"],
            proposed_secret=raw["secret"],
            permissions=frozenset(NodePermission(item) for item in permissions),
            intent=intent,
            root_public_key=(
                raw.get("root_public_key")
                if isinstance(raw.get("root_public_key"), str)
                else None
            ),
            transport_generation=(
                raw.get("transport_generation")
                if isinstance(raw.get("transport_generation"), int)
                and not isinstance(raw.get("transport_generation"), bool)
                else None
            ),
            transport_proof=(
                raw.get("transport_proof")
                if isinstance(raw.get("transport_proof"), str)
                else None
            ),
        )
        result = handler(request)
    except (KeyError, TypeError, ValueError, RemoteProtocolError):
        return json.dumps({"approved": False, "error": "denied"})
    if isinstance(result, dict):
        return json.dumps(result)
    approved = bool(result)
    return json.dumps({"approved": approved, "error": None if approved else "denied"})


def _pair_control_response(
    raw: dict[str, Any], handler: Callable[[PairingControlRequest], bool] | None
) -> str:
    if handler is None:
        return json.dumps({"approved": False, "error": "pairing_unavailable"})
    try:
        request = validate_pairing_control_request(raw)
        approved = handler(request)
    except (KeyError, TypeError, ValueError, RemoteProtocolError):
        approved = False
    return json.dumps(
        {"approved": bool(approved), "error": None if approved else "denied"}
    )


def _elevation_response(
    raw: dict[str, Any], handler: Callable[[CapabilityElevationRequest], bool] | None
) -> str:
    required = {
        "op",
        "caller_node_id",
        "identity_fingerprint",
        "transport_fingerprint",
        "current_secret",
        "secret",
        "permissions",
    }
    if handler is None or set(raw) != required:
        return json.dumps({"approved": False, "error": "elevation_unavailable"})
    try:
        permissions = raw["permissions"]
        if not isinstance(permissions, list) or any(
            not isinstance(item, str) for item in permissions
        ):
            return json.dumps({"approved": False, "error": "invalid_elevation"})
        request = CapabilityElevationRequest(
            caller_node_id=NodeId(raw["caller_node_id"]),
            identity_fingerprint=raw["identity_fingerprint"],
            transport_fingerprint=raw["transport_fingerprint"],
            current_secret=raw["current_secret"],
            proposed_secret=raw["secret"],
            permissions=frozenset(NodePermission(item) for item in permissions),
        )
        approved = handler(request)
    except (KeyError, TypeError, ValueError, RemoteProtocolError):
        approved = False
    return json.dumps(
        {"approved": bool(approved), "error": None if approved else "denied"}
    )

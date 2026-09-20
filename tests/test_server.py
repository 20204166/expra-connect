import socket
import time
import unittest
from typing import Any

from expra_connect.identity import NodeId
from expra_connect.models import NodePermission
from expra_connect.remote_service import AuthenticatedNodeProvider, PairingTransaction
from expra_connect.server import (
    PEER_SERVICE_DEFAULT_PORT,
    RemoteSocketServer,
    _elevation_response,
    _pair_request_response,
)
from expra_connect.socket_transport import SocketRemoteTransport


class _Service:
    def handle(self, payload: str) -> str:
        return payload


class ServerTests(unittest.TestCase):
    def test_admission_semaphore_is_retained_for_listener_lifecycle(self) -> None:
        server = RemoteSocketServer(_Service(), max_active_handlers=2)
        server.start()
        try:
            self.assertIsNotNone(server._admission)
            assert server._admission is not None
            self.assertEqual(server._admission._value, 2)
        finally:
            server.stop()

    def test_stop_closes_listener_when_shutdown_raises(self) -> None:
        class BrokenServer:
            def __init__(self) -> None:
                self.closed = False

            def shutdown(self) -> None:
                raise RuntimeError("shutdown failed")

            def server_close(self) -> None:
                self.closed = True

        server = RemoteSocketServer(_Service())
        broken = BrokenServer()
        server._server = broken
        server._thread = object()

        server.stop()

        self.assertTrue(broken.closed)
        self.assertIsNone(server._server)
        self.assertIsNone(server._thread)

    def test_pairing_request_with_extra_field_is_unavailable(self) -> None:
        raw = {
            "op": "pair_request",
            "pairing_mode": "transactional",
            "caller_node_id": "caller",
            "identity_fingerprint": "identity",
            "transport_fingerprint": "transport",
            "secret": "a" * 64,
            "permissions": [NodePermission.READ_STATE.value],
            "unexpected": True,
        }

        response = _pair_request_response(raw, lambda _request: True)

        self.assertEqual(
            response,
            '{"approved": false, "error": "pairing_unavailable"}',
        )

    def test_pairing_request_with_unknown_mode_is_unavailable(self) -> None:
        raw = {
            "op": "pair_request",
            "pairing_mode": "legacy",
            "caller_node_id": "caller",
            "identity_fingerprint": "identity",
            "transport_fingerprint": "transport",
            "secret": "a" * 64,
            "permissions": [NodePermission.READ_STATE.value],
        }

        response = _pair_request_response(raw, lambda _request: True)

        self.assertEqual(
            response,
            '{"approved": false, "error": "pairing_unavailable"}',
        )

    def test_elevation_request_with_non_string_permission_is_invalid(self) -> None:
        raw = {
            "op": "elevation_request",
            "caller_node_id": "caller",
            "identity_fingerprint": "identity",
            "transport_fingerprint": "transport",
            "current_secret": "a" * 64,
            "secret": "b" * 64,
            "permissions": [NodePermission.READ_STATE.value, 1],
        }

        response = _elevation_response(raw, lambda _request: True)

        self.assertEqual(
            response,
            '{"approved": false, "error": "invalid_elevation"}',
        )

    def test_elevation_request_with_unknown_permission_is_denied(self) -> None:
        raw = {
            "op": "elevation_request",
            "caller_node_id": "caller",
            "identity_fingerprint": "identity",
            "transport_fingerprint": "transport",
            "current_secret": "a" * 64,
            "secret": "b" * 64,
            "permissions": ["unknown"],
        }

        response = _elevation_response(raw, lambda _request: True)

        self.assertEqual(
            response,
            '{"approved": false, "error": "denied"}',
        )

    def test_elevation_request_with_extra_field_is_unavailable(self) -> None:
        raw = {
            "op": "elevation_request",
            "caller_node_id": "caller",
            "identity_fingerprint": "identity",
            "transport_fingerprint": "transport",
            "current_secret": "a" * 64,
            "secret": "b" * 64,
            "permissions": [NodePermission.READ_STATE.value],
            "unexpected": True,
        }

        response = _elevation_response(raw, lambda _request: True)

        self.assertEqual(
            response,
            '{"approved": false, "error": "elevation_unavailable"}',
        )

    def test_default_port_is_stable_and_non_ephemeral(self) -> None:
        self.assertEqual(PEER_SERVICE_DEFAULT_PORT, 27321)
        self.assertLess(PEER_SERVICE_DEFAULT_PORT, 32768)

    def test_idempotent_start_stop_and_framed_round_trip(self) -> None:
        server = RemoteSocketServer(_Service())
        server.start()
        server.start()
        try:
            port = server.bound_port
            self.assertIsNotNone(port)
            transport = SocketRemoteTransport("127.0.0.1", port or 0)
            self.assertEqual(transport.request('{"ok":true}'), '{"ok":true}')
        finally:
            server.stop()
            server.stop()

    def test_busy_preferred_port_falls_back(self) -> None:
        with socket.socket() as blocker:
            blocker.bind(("127.0.0.1", 0))
            blocker.listen()
            blocked_port = blocker.getsockname()[1]
            server = RemoteSocketServer(_Service(), preferred_port=blocked_port)
            try:
                server.start()
                self.assertNotEqual(server.bound_port, blocked_port)
                self.assertFalse(server.preferred_port_honored)
            finally:
                server.stop()

    def test_rejected_service_does_not_send_a_response(self) -> None:
        class Failing:
            def handle(self, payload: str) -> str:
                raise ValueError(payload)

        server = RemoteSocketServer(Failing())
        server.start()
        try:
            with socket.create_connection(
                ("127.0.0.1", server.bound_port or 0)
            ) as peer:
                peer.settimeout(1.0)
                peer.sendall(b"\x00\x00\x00\x01x")
                time.sleep(0.05)
                self.assertEqual(peer.recv(1), b"")
        finally:
            server.stop()

    def test_transactional_pairing_request_reaches_explicit_handler(self) -> None:
        received: list[Any] = []

        def approve(request: Any) -> dict[str, Any]:
            received.append(request)
            return {"approved": True, "transaction_id": "tx"}

        server = RemoteSocketServer(
            _Service(),
            pairing_handler=approve,
        )
        server.start()
        try:
            transport = SocketRemoteTransport("127.0.0.1", server.bound_port or 0)
            response = AuthenticatedNodeProvider.request_pairing(
                transport=transport,
                caller_node_id=NodeId("caller"),
                identity_fingerprint="identity",
                transport_fingerprint="transport",
                proposed_secret="a" * 64,
                permissions=frozenset({NodePermission.READ_STATE}),
            )
            if not isinstance(response, dict):
                self.fail("pairing handler did not return a structured response")
            self.assertEqual(response["transaction_id"], "tx")
            self.assertEqual(received[0].caller_node_id, NodeId("caller"))
        finally:
            server.stop()

    def test_pairing_control_requires_exact_binding_and_calls_handler(self) -> None:
        received: list[Any] = []

        def confirm(request: Any) -> bool:
            received.append(request)
            return True

        server = RemoteSocketServer(_Service(), pair_confirm_handler=confirm)
        server.start()
        try:
            transport = SocketRemoteTransport("127.0.0.1", server.bound_port or 0)
            transaction = PairingTransaction(
                transaction_id="tx",
                caller_node_id="caller",
                identity_fingerprint="identity",
                transport_fingerprint="transport",
                secret="a" * 64,
                permissions=frozenset({NodePermission.READ_STATE}),
                expires_at=9999999999.0,
                transport=transport,
            )
            self.assertTrue(AuthenticatedNodeProvider.confirm_pairing(transaction))
            self.assertEqual(received[0].transaction_id, "tx")
        finally:
            server.stop()

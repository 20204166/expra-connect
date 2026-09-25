import time
import unittest
from threading import Event
from typing import Any, cast

from expra_connect.server import PeerServer
from expra_connect.transport import SocketTransport, TransportError
from expra_connect.wire_protocol import RemoteExecutionError, RemoteProtocolError


class TransportTests(unittest.TestCase):
    def test_socket_transport_round_trips_a_delayed_response(self) -> None:
        server = PeerServer("127.0.0.1", 0, lambda request: {"echo": request["value"]})
        server.start()
        try:
            host, port = server.address
            result = SocketTransport(host, port, timeout=2.0).request({"value": "ok"})
            self.assertEqual(result, {"echo": "ok"})
        finally:
            server.stop()

    def test_delayed_response_after_poll_interval_is_allowed(self) -> None:
        def delayed(_request: dict[str, object]) -> dict[str, object]:
            time.sleep(0.3)
            return {"ok": True}

        server = PeerServer("127.0.0.1", 0, delayed)
        server.start()
        try:
            host, port = server.address
            self.assertEqual(
                SocketTransport(host, port, timeout=1.0).request({}), {"ok": True}
            )
        finally:
            server.stop()

    def test_cancelled_request_is_rejected_before_connect(self) -> None:
        cancelled = Event()
        cancelled.set()
        with self.assertRaises(RemoteExecutionError):
            SocketTransport("127.0.0.1", 1).request({}, cancel_event=cancelled)

    def test_connection_failures_are_normalized(self) -> None:
        with self.assertRaises(TransportError):
            SocketTransport("127.0.0.1", 1, timeout=0.1).request({})

    def test_response_must_be_a_json_object(self) -> None:
        server = PeerServer(
            "127.0.0.1", 0, lambda _request: cast(Any, ["not", "an object"])
        )
        server.start()
        try:
            host, port = server.address
            with self.assertRaises(RemoteProtocolError):
                SocketTransport(host, port).request({})
        finally:
            server.stop()

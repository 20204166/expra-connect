import math
import ssl
import struct
import unittest
from threading import Event
from typing import Any, Literal, cast
from unittest.mock import Mock, patch

from expra_connect.identity import NodeId
from expra_connect.models import TrustedNodeRecord
from expra_connect.socket_transport import (
    MemoryRemoteTransport,
    SocketRemoteTransport,
    _invoke_with_optional_cancel,
    _recv_exact,
    _recv_frame,
    _send_frame,
    build_trusted_transport,
    request_with_retry,
)
from expra_connect.wire_protocol import (
    RemoteAuthError,
    RemoteExecutionError,
    RemoteProtocolError,
    RemoteTransportError,
)


class _ChunkSocket:
    def __init__(self, chunks: list[bytes]) -> None:
        self.chunks = list(chunks)
        self.sent: list[bytes] = []

    def recv(self, length: int) -> bytes:
        if not self.chunks:
            return b""
        chunk = self.chunks.pop(0)
        if len(chunk) <= length:
            return chunk
        self.chunks.insert(0, chunk[length:])
        return chunk[:length]

    def sendall(self, payload: bytes) -> None:
        self.sent.append(payload)


class SocketTransportConfigurationTests(unittest.TestCase):
    def test_constructor_rejects_invalid_endpoint_and_timeout_values(self) -> None:
        invalid = (
            (None, 1, 1.0),
            (" ", 1, 1.0),
            ("host", True, 1.0),
            ("host", 0, 1.0),
            ("host", 65536, 1.0),
            ("host", 1, True),
            ("host", 1, 0),
            ("host", 1, -1.0),
            ("host", 1, math.nan),
            ("host", 1, math.inf),
        )
        for host, port, timeout in invalid:
            with (
                self.subTest(host=host, port=port, timeout=timeout),
                self.assertRaises((TypeError, ValueError)),
            ):
                SocketRemoteTransport(cast(str, host), port, timeout=timeout)

    def test_fingerprint_requires_tls_and_nonempty_text(self) -> None:
        with self.assertRaises(ValueError):
            SocketRemoteTransport("host", 1, expected_fingerprint=" ")
        with self.assertRaises(ValueError):
            SocketRemoteTransport("host", 1, expected_fingerprint="pin")

    def test_cert_none_context_requires_a_pin(self) -> None:
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
        with self.assertRaises(ValueError):
            SocketRemoteTransport("host", 1, ssl_context=context)

    def test_verifying_context_may_omit_application_pin(self) -> None:
        context = ssl.create_default_context()
        transport = SocketRemoteTransport("host", 1, ssl_context=context)
        self.assertIs(transport._ssl_context, context)

    def test_tls_transport_requires_a_nonempty_pin(self) -> None:
        with self.assertRaises((TypeError, ValueError)):
            from expra_connect.socket_transport import TLSRemoteTransport

            TLSRemoteTransport("host", 1, expected_fingerprint="")


class FrameContractTests(unittest.TestCase):
    def test_fragmented_header_and_body_are_reassembled(self) -> None:
        wire = struct.pack(">I", 5) + b"hello"
        sock = _ChunkSocket([wire[:1], wire[1:3], wire[3:4], wire[4:6], wire[6:]])
        self.assertEqual(
            _recv_frame(sock, max_bytes=5, closed_message="closed"), b"hello"
        )

    def test_truncated_header_and_body_use_closed_message(self) -> None:
        with self.assertRaisesRegex(RemoteTransportError, "header closed"):
            _recv_frame(
                _ChunkSocket([b"\x00\x00"]), max_bytes=5, closed_message="header closed"
            )
        with self.assertRaisesRegex(RemoteTransportError, "body closed"):
            _recv_frame(
                _ChunkSocket([struct.pack(">I", 5), b"hi"]),
                max_bytes=5,
                closed_message="body closed",
            )

    def test_frame_limits_and_invalid_lengths_fail_closed(self) -> None:
        with self.assertRaises(RemoteTransportError):
            _recv_frame(
                _ChunkSocket([struct.pack(">I", 6)]),
                max_bytes=5,
                closed_message="closed",
            )
        with self.assertRaises(RemoteProtocolError):
            _recv_frame(
                _ChunkSocket([struct.pack(">I", 0)]),
                max_bytes=5,
                closed_message="closed",
            )
        with self.assertRaises((TypeError, ValueError)):
            _recv_exact(_ChunkSocket([]), True)

    def test_send_frame_rejects_wrong_payload_and_oversized_payload(self) -> None:
        with self.assertRaises(TypeError):
            _send_frame(Mock(), cast(Any, "text"), max_bytes=5)
        with self.assertRaises(RemoteTransportError):
            _send_frame(Mock(), b"123456", max_bytes=5)


class RetryAndCallbackTests(unittest.TestCase):
    def test_retry_validates_attempts_and_checks_cancellation(self) -> None:
        for attempts in (0, -1, True):
            with (
                self.subTest(attempts=attempts),
                self.assertRaises((TypeError, ValueError)),
            ):
                request_with_retry(Mock(), "same", Event(), attempts)
        cancelled = Event()
        cancelled.set()
        request = Mock()
        with self.assertRaises(RemoteExecutionError):
            request_with_retry(request, "same", cancelled, 2)
        request.assert_not_called()

    def test_retry_reuses_exact_envelope_and_only_retries_transport_errors(
        self,
    ) -> None:
        request = Mock(side_effect=[RemoteTransportError("lost"), "ok"])
        envelope = "same"
        self.assertEqual(request_with_retry(request, envelope, None, 2), "ok")
        self.assertEqual(request.call_args_list[0].args[0], envelope)
        self.assertEqual(request.call_args_list[1].args[0], envelope)
        for error in (
            RemoteAuthError("auth"),
            RemoteProtocolError("protocol"),
            RemoteExecutionError("cancel"),
        ):
            request = Mock(side_effect=error)
            with self.subTest(error=type(error)), self.assertRaises(type(error)):
                request_with_retry(request, "same", None, 2)
            request.assert_called_once()

    def test_optional_cancel_does_not_retry_internal_type_error(self) -> None:
        calls = 0

        def request(_envelope: str, _cancel: object) -> str:
            nonlocal calls
            calls += 1
            raise TypeError("inside request")

        with self.assertRaisesRegex(TypeError, "inside request"):
            _invoke_with_optional_cancel(request, "same", None)
        self.assertEqual(calls, 1)

    def test_optional_cancel_supports_one_two_args_varargs_and_keyword_only(
        self,
    ) -> None:
        event = Event()
        self.assertEqual(
            _invoke_with_optional_cancel(lambda value: value, "one", event), "one"
        )
        self.assertEqual(
            _invoke_with_optional_cancel(lambda value, cancel: value, "two", event),
            "two",
        )
        self.assertEqual(
            _invoke_with_optional_cancel(lambda *args: args[0], "many", event), "many"
        )

        def keyword_only(value: str, *, cancel_event: object) -> str:
            return value

        self.assertEqual(
            _invoke_with_optional_cancel(keyword_only, "keyword", event), "keyword"
        )


class MemoryTransportTests(unittest.TestCase):
    def test_non_string_service_response_is_a_protocol_error(self) -> None:
        with self.assertRaises(RemoteProtocolError):
            MemoryRemoteTransport(Mock(handle=Mock(return_value={"ok": True}))).request(
                "x"
            )


class TrustedTransportTests(unittest.TestCase):
    def test_trusted_record_validation_precedes_factory(self) -> None:
        record = TrustedNodeRecord(NodeId("peer"), "host", 123, "secret", "pin")
        factory = Mock()
        self.assertIs(
            build_trusted_transport(record, transport_cls=factory), factory.return_value
        )
        factory.assert_called_once_with("host", 123, expected_fingerprint="pin")
        for invalid in (
            TrustedNodeRecord(NodeId("peer"), " ", 123, "secret", "pin"),
            TrustedNodeRecord(NodeId("peer"), "host", True, "secret", "pin"),
            TrustedNodeRecord(NodeId("peer"), "host", 0, "secret", "pin"),
            TrustedNodeRecord(NodeId("peer"), "host", 65536, "secret", "pin"),
            TrustedNodeRecord(NodeId("peer"), "host", 123, "secret", ""),
        ):
            factory.reset_mock()
            with self.subTest(invalid=invalid), self.assertRaises(RemoteAuthError):
                build_trusted_transport(invalid, transport_cls=factory)
            factory.assert_not_called()


class RequestLifecycleTests(unittest.TestCase):
    class RawSocket:
        def __init__(self) -> None:
            self.closed = False
            self.timeouts: list[float] = []

        def __enter__(self) -> "RequestLifecycleTests.RawSocket":
            return self

        def __exit__(self, *_args: object) -> Literal[False]:
            self.close()
            return False

        def settimeout(self, value: float) -> None:
            self.timeouts.append(value)

        def close(self) -> None:
            self.closed = True

    class WrappedSocket:
        def __init__(self, response: bytes = b"\x00\x00\x00\x02ok") -> None:
            self.response = response
            self.closed = False
            self.sent: list[bytes] = []
            self.timeouts: list[float] = []

        def settimeout(self, value: float) -> None:
            self.timeouts.append(value)

        def getpeercert(self, *, binary_form: bool) -> bytes:
            del binary_form
            return b"certificate"

        def sendall(self, payload: bytes) -> None:
            self.sent.append(payload)

        def recv(self, length: int) -> bytes:
            result, self.response = self.response[:length], self.response[length:]
            return result

        def close(self) -> None:
            self.closed = True

    def test_connect_uses_remaining_total_deadline(self) -> None:
        connect_timeouts: list[float] = []

        def fail_connect(_address: object, timeout: float) -> None:
            connect_timeouts.append(timeout)
            raise OSError("refused")

        with (
            patch(
                "expra_connect.socket_transport.time.monotonic",
                side_effect=[10.0, 12.0],
            ),
            patch(
                "expra_connect.socket_transport.socket_module.create_connection",
                side_effect=fail_connect,
            ),
            self.assertRaises(RemoteTransportError),
        ):
            SocketRemoteTransport("host", 1, timeout=5).request("x")
        self.assertEqual(connect_timeouts, [3.0])

    def test_pin_mismatch_and_missing_certificate_send_no_application_bytes(
        self,
    ) -> None:
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
        for certificate in (b"certificate", None):
            raw = self.RawSocket()
            wrapped = self.WrappedSocket()
            if certificate is None:
                cast(Any, wrapped).getpeercert = lambda *, binary_form: None
            with (
                self.subTest(certificate=certificate),
                patch(
                    "expra_connect.socket_transport.socket_module.create_connection",
                    return_value=raw,
                ),
                patch.object(context, "wrap_socket", return_value=wrapped),
                patch(
                    "expra_connect.socket_transport.certificate_fingerprint",
                    return_value="different",
                ),
                self.assertRaises(RemoteAuthError),
            ):
                SocketRemoteTransport(
                    "host", 1, ssl_context=context, expected_fingerprint="expected"
                ).request("payload")
            self.assertEqual(wrapped.sent, [])
            self.assertTrue(raw.closed)
            self.assertTrue(wrapped.closed)

    def test_certificate_verification_failure_is_authentication_error(self) -> None:
        context = ssl.create_default_context()
        raw = self.RawSocket()
        with (
            patch(
                "expra_connect.socket_transport.socket_module.create_connection",
                return_value=raw,
            ),
            patch.object(
                context,
                "wrap_socket",
                side_effect=ssl.SSLCertVerificationError("untrusted"),
            ),
            self.assertRaises(RemoteAuthError),
        ):
            SocketRemoteTransport("host", 1, ssl_context=context).request("payload")
        self.assertTrue(raw.closed)

    def test_timeout_is_normalized_and_cancelled_timeout_wins(self) -> None:
        for cancel in (False, True):
            event = Event()
            if cancel:
                event.set()
            with (
                self.subTest(cancel=cancel),
                patch(
                    "expra_connect.socket_transport.socket_module.create_connection",
                    side_effect=TimeoutError("platform text"),
                ),
            ):
                expected = RemoteExecutionError if cancel else RemoteTransportError
                with self.assertRaisesRegex(expected, "cancelled|timed out"):
                    SocketRemoteTransport("host", 1).request("payload", event)

    def test_request_rejects_non_text_envelope(self) -> None:
        with self.assertRaises(TypeError):
            SocketRemoteTransport("host", 1).request(b"payload")  # type: ignore[arg-type]

    def test_socket_closes_after_send_failure_and_invalid_utf8(self) -> None:
        class FailingSocket(RequestLifecycleTests.RawSocket):
            def sendall(self, _payload: bytes) -> None:
                raise OSError("send failed")

        send_failure = FailingSocket()
        with (
            patch(
                "expra_connect.socket_transport.socket_module.create_connection",
                return_value=send_failure,
            ),
            self.assertRaises(RemoteTransportError),
        ):
            SocketRemoteTransport("host", 1).request("payload")
        self.assertTrue(send_failure.closed)

        class InvalidResponseSocket(RequestLifecycleTests.RawSocket):
            def __init__(self) -> None:
                super().__init__()
                self.response = b"\x00\x00\x00\x01\xff"

            def sendall(self, _payload: bytes) -> None:
                return None

            def recv(self, length: int) -> bytes:
                result, self.response = self.response[:length], self.response[length:]
                return result

        invalid_response = InvalidResponseSocket()
        with (
            patch(
                "expra_connect.socket_transport.socket_module.create_connection",
                return_value=invalid_response,
            ),
            self.assertRaises(RemoteProtocolError),
        ):
            SocketRemoteTransport("host", 1).request("payload")
        self.assertTrue(invalid_response.closed)

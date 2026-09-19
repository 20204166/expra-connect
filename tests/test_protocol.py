import socket
import unittest

from expra_connect.identity import NodeId
from expra_connect.models import NodePermission
from expra_connect.protocol import (
    MAX_FRAME,
    RemoteAuthError,
    RemoteTransportError,
    ReplayCache,
    decode_frame,
    encode_frame,
    sign_request,
    verify_request,
)
from expra_connect.wire_protocol import (
    PairingRequest,
    RemoteAuthorizationError,
    RemoteProtocolError,
    parse_hello_capabilities,
    sign_response,
    validate_operation_params,
    verify_response,
)


class ProtocolTests(unittest.TestCase):
    def test_frame_round_trip(self) -> None:
        payload = b'{"operation":"ping"}'
        left, right = socket.socketpair()
        try:
            encode_frame(left, payload, max_bytes=MAX_FRAME)
            self.assertEqual(
                decode_frame(
                    right,
                    max_bytes=MAX_FRAME,
                    closed_message="closed",
                ),
                payload,
            )
        finally:
            left.close()
            right.close()

    def test_oversized_frame_is_rejected(self) -> None:
        with self.assertRaises(RemoteTransportError):
            encode_frame(
                None,
                b"x" * (MAX_FRAME + 1),
                max_bytes=MAX_FRAME,
            )

    def test_signed_request_verifies_once_and_replay_is_rejected(self) -> None:
        envelope = sign_request(
            node_id="peer-a",
            op="ping",
            params={},
            request_id="request-1",
            nonce="nonce-1",
            timestamp=100.0,
            secret="secret",
        )
        cache = ReplayCache(clock=lambda: 100.0)
        request = verify_request(
            envelope,
            secret="secret",
            clock=lambda: 100.0,
            freshness_seconds=60.0,
            replay_cache=cache,
        )
        self.assertEqual(request.node_id.value, "peer-a")
        with self.assertRaises(RemoteAuthError):
            verify_request(
                envelope,
                secret="secret",
                clock=lambda: 100.0,
                freshness_seconds=60.0,
                replay_cache=cache,
            )

    def test_unknown_hello_capabilities_are_metadata_only(self) -> None:
        capabilities = parse_hello_capabilities(
            {"capabilities": ["read_state", "future_capability"]}
        )
        self.assertEqual({item.value for item in capabilities}, {"read_state"})

    def test_pairing_request_rejects_destructive_permissions(self) -> None:
        with self.assertRaises(RemoteAuthorizationError):
            PairingRequest(
                caller_node_id=NodeId("peer-b"),
                identity_fingerprint="identity",
                transport_fingerprint="transport",
                proposed_secret="a" * 64,
                permissions=frozenset({NodePermission.CLEANUP}),
            )

    def test_operation_validation_rejects_extra_fields_and_bad_fence(self) -> None:
        with self.assertRaises(RemoteProtocolError):
            validate_operation_params("ping", {"unexpected": True})
        with self.assertRaises(RemoteProtocolError):
            validate_operation_params(
                "assign_role",
                {"cluster_id": "c", "epoch": True, "fencing_token": "t"},
            )

    def test_response_signature_and_freshness_are_verified(self) -> None:
        envelope = sign_response(
            node_id="peer-a",
            request_id="request-1",
            status="ok",
            payload={"ready": True},
            timestamp=100.0,
            secret="secret",
        )
        response = verify_response(
            envelope,
            secret="secret",
            clock=lambda: 100.0,
            freshness_seconds=60.0,
        )
        self.assertEqual(response.payload, {"ready": True})
        envelope["payload"] = {"ready": False}
        with self.assertRaises(RemoteAuthError):
            verify_response(
                envelope,
                secret="secret",
                clock=lambda: 100.0,
                freshness_seconds=60.0,
            )

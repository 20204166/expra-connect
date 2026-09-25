import unittest
import unittest.mock
from enum import Enum
from typing import Any, cast
from unittest.mock import Mock

import run_peer
from expra_connect import NodeId
from expra_connect.wire_protocol import RemoteAuthorizationError
from run_peer import format_terminal_event


class RunPeerTerminalOutputTests(unittest.TestCase):
    def test_started_event_is_ordered_and_includes_version(self) -> None:
        line = format_terminal_event(
            1,
            "started",
            role="initiator",
            version="0.8.0.0",
            state="started",
        )
        self.assertEqual(
            line,
            "[01] started role=initiator version=0.8.0.0 state=started",
        )

    def test_started_event_handles_missing_values(self) -> None:
        self.assertEqual(
            format_terminal_event(2, "started"),
            "[02] started role=- version=- state=-",
        )

    def test_candidate_discovery_prints_first_address_without_payload(self) -> None:
        line = format_terminal_event(
            3,
            "discovery_event",
            kind="candidate",
            payload={
                "addresses": ["192.168.55.107", "10.0.0.2"],
                "port": 27321,
                "endpoint_candidates": [{"source": "ipv4"}],
                "root_public_key": "public-key-that-must-not-print",
            },
        )
        self.assertEqual(
            line,
            "[03] discovered address=192.168.55.107 port=27321 source=ipv4",
        )
        self.assertNotIn("public-key", line)

    def test_candidate_discovery_handles_empty_endpoints(self) -> None:
        self.assertEqual(
            format_terminal_event(
                4,
                "discovery_event",
                kind="candidate",
                payload={"addresses": [], "endpoint_candidates": []},
            ),
            "[04] discovered address=- port=- source=-",
        )

    def test_route_event_prints_address_phase_and_outcome(self) -> None:
        self.assertEqual(
            format_terminal_event(
                5,
                "route_attempt",
                phase="connection",
                address="192.168.55.107",
                port=27321,
                source="ipv4",
                outcome="succeeded",
                duration_ms=38.4,
            ),
            "[05] route phase=connection outcome=succeeded "
            "address=192.168.55.107 port=27321 source=ipv4 latency_ms=38.4",
        )

    def test_route_event_omits_latency_when_not_available(self) -> None:
        self.assertEqual(
            format_terminal_event(
                6,
                "route_attempt",
                phase="pairing",
                address="192.168.55.107",
                port=27321,
                source="ipv4",
                outcome="started",
            ),
            "[06] route phase=pairing outcome=started "
            "address=192.168.55.107 port=27321 source=ipv4",
        )

    def test_enum_values_use_their_stable_value(self) -> None:
        source = cast(Any, Enum("Source", {"IPV4": "ipv4"})).IPV4
        self.assertIn(
            "source=ipv4",
            format_terminal_event(
                12,
                "route_attempt",
                phase="connection",
                address="192.168.55.107",
                port=27321,
                source=source,
                outcome="started",
            ),
        )

    def test_pairing_request_abbreviates_caller_and_sorts_permissions(self) -> None:
        self.assertEqual(
            format_terminal_event(
                7,
                "pairing_request",
                caller_node_id="1234567890abcdef",
                permissions=["write", "read_state"],
            ),
            "[07] pairing_request caller=12345678... permissions=read_state,write",
        )

    def test_connection_event_includes_trust_state_without_proof(self) -> None:
        line = format_terminal_event(
            8,
            "connected",
            peer_id="1234567890abcdef",
            tls_verified=True,
            generation=2,
            transport_proof="proof-must-not-print",
        )
        self.assertEqual(
            line,
            "[08] connected peer=12345678... tls_verified=true generation=2",
        )
        self.assertNotIn("proof", line)

    def test_shared_result_reports_status_without_result_payload(self) -> None:
        line = format_terminal_event(
            9,
            "shared_capability_result",
            result={"ok": True, "peer_id": "secret-id", "params": {"x": 1}},
        )
        self.assertEqual(
            line,
            "[09] shared capability=test.read_state outcome=success",
        )
        self.assertNotIn("secret-id", line)

    def test_error_and_timeout_events_are_summarized(self) -> None:
        self.assertEqual(
            format_terminal_event(
                10,
                "error",
                error_type="RemoteAuthError",
                error="secret-bearing detail",
            ),
            "[10] error type=RemoteAuthError",
        )
        self.assertEqual(
            format_terminal_event(11, "discovery_timeout", peers=[{"secret": 1}]),
            "[11] discovery_timeout peers=1",
        )

    def test_target_rotation_reauthorizes_only_explicit_harness_grants(self) -> None:
        runtime = Mock()
        peer_id = NodeId("initiator")

        run_peer._reallow_capability_after_rotation(runtime, {peer_id})

        runtime.sharing.allow.assert_called_once_with(peer_id, run_peer.CAPABILITY)

    def test_post_rotation_share_retries_bounded_authorization_window(self) -> None:
        operation = Mock(side_effect=[RemoteAuthorizationError("pending"), "ok"])
        retry = getattr(run_peer, "_retry_shared_capability", None)
        self.assertTrue(callable(retry))

        with unittest.mock.patch("run_peer.time.sleep"):
            assert callable(retry)
            self.assertEqual(retry(operation), "ok")
        self.assertEqual(operation.call_count, 2)


if __name__ == "__main__":
    unittest.main()

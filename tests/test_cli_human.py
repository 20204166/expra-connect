import unittest

from expra_connect.cli import format_human_output


class HumanCliOutputTests(unittest.TestCase):
    def test_status_output_is_ordered(self) -> None:
        self.assertEqual(
            format_human_output(
                "status",
                {
                    "state": "started",
                    "listener_started": True,
                    "discovery_started": True,
                    "bound_host": "0.0.0.0",
                    "bound_port": 27321,
                    "tls_fingerprint": "abcd",
                },
            ),
            "state=started\nlistener=started\ndiscovery=started\n"
            "bound=0.0.0.0:27321\ntls_fingerprint=abcd",
        )

    def test_status_handles_stopped_missing_values(self) -> None:
        self.assertIn("state=stopped", format_human_output("status", {"state": "stopped"}))
        self.assertIn("listener=not_started", format_human_output("status", {}))

    def test_identity_keeps_developer_identifiers(self) -> None:
        self.assertEqual(
            format_human_output(
                "identity", {"node_id": "node-a", "identity_fingerprint": "fingerprint"}
            ),
            "node_id=node-a\nidentity_fingerprint=fingerprint",
        )

    def test_peer_output_keeps_addresses_and_endpoint_details(self) -> None:
        output = format_human_output(
            "peers",
            [
                {
                    "stable_id": "peer-a",
                    "hostname": "peer-host",
                    "endpoint_candidates": [
                        {"address": "192.168.55.107", "port": 27321, "source": "ipv4"}
                    ],
                    "root_public_key": "must-not-print",
                    "transport_proof": "must-not-print",
                }
            ],
        )
        self.assertIn("peer[1] id=peer-a host=peer-host", output)
        self.assertIn("endpoint=192.168.55.107:27321 source=ipv4", output)
        self.assertNotIn("must-not-print", output)

    def test_peer_output_handles_empty_list(self) -> None:
        self.assertEqual(format_human_output("peers", []), "peers=0")

    def test_diagnostics_preserves_section_order(self) -> None:
        output = format_human_output(
            "diagnostics",
            {
                "version": "0.8.0.0",
                "status": {"state": "started"},
                "identity": {"node_id": "local"},
                "transport": {"current_generation": 2},
                "peers": [],
                "trust": {"trusted_peers": ["peer-a"], "pending_pairings": []},
                "observability": {"metrics": []},
            },
        )
        self.assertLess(output.index("version=0.8.0.0"), output.index("status:"))
        self.assertLess(output.index("status:"), output.index("identity:"))
        self.assertLess(output.index("identity:"), output.index("transport:"))
        self.assertLess(output.index("transport:"), output.index("trust:"))

    def test_diagnostics_prints_observability_metrics(self) -> None:
        output = format_human_output(
            "diagnostics",
            {
                "version": "0.8.0.0",
                "status": {},
                "transport": {},
                "peers": [],
                "observability": {
                    "metrics": [
                        {"target": "remote:hello", "count": 3, "successes": 2, "failures": 1}
                    ]
                },
            },
        )
        self.assertIn("metric target=remote:hello count=3 success=2 failure=1", output)

    def test_pair_output_keeps_permissions(self) -> None:
        self.assertEqual(
            format_human_output(
                "pair", {"ok": True, "peer_id": "peer-a", "permissions": ["read_state"]}
            ),
            "ok=true\npeer_id=peer-a\npermissions=read_state",
        )

    def test_revoke_output_reports_result(self) -> None:
        self.assertEqual(
            format_human_output("revoke", {"ok": True, "revoked": "peer-a"}),
            "ok=true\nrevoked=peer-a",
        )

    def test_unknown_command_and_sensitive_top_level_values_are_bounded(self) -> None:
        output = format_human_output(
            "unknown", {"ok": True, "secret": "must-not-print", "value": 3}
        )
        self.assertEqual(output, "ok=true\nvalue=3")
        self.assertNotIn("secret", output)


if __name__ == "__main__":
    unittest.main()

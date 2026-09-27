import json
import tempfile
import unittest
from pathlib import Path

from peer_harness import analyzer
from peer_harness.cli import _parser, main

TARGET_EVENTS = [
    {
        "event": "started",
        "role": "target",
        "node_id": "target-node",
        "state": "started",
    },
    {
        "event": "pairing_request",
        "caller_node_id": "initiator-node",
        "permissions": ["read_state"],
    },
    {
        "event": "capability_granted",
        "capability": "test.read_state",
        "peer_id": "initiator-node",
        "reason": "pairing",
    },
]

INITIATOR_EVENTS = [
    {
        "event": "started",
        "role": "initiator",
        "node_id": "initiator-node",
        "state": "started",
    },
    {"event": "paired", "peer_id": "target-node", "permissions": ["read_state"]},
    {"event": "connected", "peer_id": "target-node", "tls_verified": True},
]


def _write(report: Path, events: object) -> str:
    report.write_text(json.dumps(events), encoding="utf-8")
    return str(report)


def _codes(report: dict[str, object]) -> list[str]:
    findings = report.get("role_correlation_findings")
    assert isinstance(findings, list)
    return [str(finding["code"]) for finding in findings]


class AnalyzerCliTests(unittest.TestCase):
    def test_parser_accepts_analyze_role_without_profile(self) -> None:
        args = _parser().parse_args(["analyze", "a.json", "b.json"])
        self.assertEqual(args.role_pos, "analyze")
        self.assertIsNone(args.profile)
        self.assertEqual(args.inputs, ["a.json", "b.json"])

    def test_analyze_role_does_not_require_profile(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            target = _write(Path(directory) / "target.json", TARGET_EVENTS)
            initiator = _write(Path(directory) / "initiator.json", INITIATOR_EVENTS)
            self.assertEqual(main(["analyze", target, initiator]), 0)

    def test_analyze_without_inputs_fails_closed(self) -> None:
        self.assertEqual(main(["analyze"]), 1)


class RoleCorrelationTests(unittest.TestCase):
    def test_matching_target_and_initiator_correlate_in_either_order(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            target = _write(Path(directory) / "target.json", TARGET_EVENTS)
            initiator = _write(Path(directory) / "initiator.json", INITIATOR_EVENTS)

            for order in ((target, initiator), (initiator, target)):
                with self.subTest(order=order):
                    report = analyzer.analyze(list(order))
                    codes = _codes(report)
                    self.assertIn("initiator_peer_matched", codes)
                    self.assertIn("target_caller_matched", codes)
                    for finding in report["role_correlation_findings"]:
                        self.assertNotEqual(finding["severity"], "warning")
                        self.assertNotEqual(finding["severity"], "error")

    def test_initiator_peer_not_a_target_is_reported(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            target = _write(Path(directory) / "target.json", TARGET_EVENTS)
            initiator_events = [
                {
                    "event": "started",
                    "role": "initiator",
                    "node_id": "initiator-node",
                    "state": "started",
                },
                {"event": "paired", "peer_id": "other-node"},
                {"event": "connected", "peer_id": "other-node", "tls_verified": True},
            ]
            initiator = _write(Path(directory) / "initiator.json", initiator_events)

            report = analyzer.analyze([target, initiator])
            codes = _codes(report)
            self.assertIn("initiator_peer_not_a_target", codes)
            self.assertNotIn("initiator_peer_matched", codes)

    def test_target_caller_not_an_initiator_is_reported(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            target_events = [
                {
                    "event": "started",
                    "role": "target",
                    "node_id": "target-node",
                    "state": "started",
                },
                {
                    "event": "pairing_request",
                    "caller_node_id": "stranger-node",
                    "permissions": ["read_state"],
                },
            ]
            target = _write(Path(directory) / "target.json", target_events)
            initiator = _write(Path(directory) / "initiator.json", INITIATOR_EVENTS)

            report = analyzer.analyze([target, initiator])
            codes = _codes(report)
            self.assertIn("target_caller_not_an_initiator", codes)

    def test_single_role_reports_info_not_warning(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            target = _write(Path(directory) / "target.json", TARGET_EVENTS)
            report = analyzer.analyze([target])
            self.assertIn("single_role_reported", _codes(report))
            for finding in report["role_correlation_findings"]:
                self.assertEqual(finding["severity"], "info")


if __name__ == "__main__":
    unittest.main()

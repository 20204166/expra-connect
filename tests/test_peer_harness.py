import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

from peer_harness.cli import _parser
from peer_harness.flows import CAPABILITY, _explicit_approval_decision

from expra_connect import NodeId


class ExplicitApprovalTests(unittest.TestCase):
    def _request(self, caller: str = "initiator") -> SimpleNamespace:
        return SimpleNamespace(
            caller_node_id=NodeId(caller),
            permissions=(SimpleNamespace(value="read_state"),),
        )

    def test_parser_accepts_explicit_approval_flags(self) -> None:
        args = _parser().parse_args(
            [
                "--role",
                "target",
                "--profile",
                "/tmp/profile",
                "--explicit-approval",
                "--approval-file",
                "/tmp/approve",
                "--approval-wait",
                "5",
            ]
        )
        self.assertTrue(args.explicit_approval)
        self.assertEqual(args.approval_file, Path("/tmp/approve"))
        self.assertEqual(args.approval_wait, 5.0)

    def test_parser_accepts_positional_role_and_preserves_role_option(self) -> None:
        try:
            target = _parser().parse_args(["target", "--profile", "/tmp/target"])
        except SystemExit:
            self.fail("parser rejected the positional role shorthand")
        legacy = _parser().parse_args(
            ["--role", "initiator", "--profile", "/tmp/initiator"]
        )

        self.assertEqual(target.role_pos, "target")
        self.assertIsNone(target.role_option)
        self.assertIsNone(legacy.role_pos)
        self.assertEqual(legacy.role_option, "initiator")

    def test_missing_approval_file_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            report = Path(directory) / "report.json"
            approval = Path(directory) / "approve"
            runtime = Mock()
            peers: set[NodeId] = set()

            approved = _explicit_approval_decision(
                runtime,
                report,
                self._request(),
                peers,
                approval_file=approval,
                approval_wait=0.2,
            )

            self.assertFalse(approved)
            runtime.sharing.allow.assert_not_called()
            self.assertEqual(peers, set())
            records = json.loads(report.read_text(encoding="utf-8"))
            self.assertEqual(
                [record["event"] for record in records],
                ["pairing_pending", "pairing_denied"],
            )

    def test_present_approval_file_allows_and_consumes_it(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            report = Path(directory) / "report.json"
            approval = Path(directory) / "approve"
            approval.write_text("approved", encoding="utf-8")
            runtime = Mock()
            peers: set[NodeId] = set()

            approved = _explicit_approval_decision(
                runtime,
                report,
                self._request(),
                peers,
                approval_file=approval,
                approval_wait=0.2,
            )

            self.assertTrue(approved)
            runtime.sharing.allow.assert_called_once_with(
                self._request().caller_node_id, CAPABILITY
            )
            self.assertIn(self._request().caller_node_id, peers)
            self.assertFalse(approval.exists())
            records = json.loads(report.read_text(encoding="utf-8"))
            self.assertEqual(
                [record["event"] for record in records],
                ["pairing_pending", "pairing_approved"],
            )


if __name__ == "__main__":
    unittest.main()

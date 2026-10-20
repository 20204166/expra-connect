from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class ConnectPerformanceBenchmarkTests(unittest.TestCase):
    def test_cli_measures_deterministic_feature_workloads(self) -> None:
        expected = {
            "discovery candidate validation",
            "discovery preferred endpoint",
            "discovery registry add + expire",
            "pairing relationship projection",
            "pairing trusted-candidate classification",
            "pairing next-generation proof classification",
            "capability authorized dispatch",
            "surface peer-granted dispatch",
            "surface cluster-granted dispatch",
        }
        with tempfile.TemporaryDirectory() as directory:
            output_path = Path(directory) / "connect.json"
            completed = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "tools.connect_performance_benchmark",
                    "--warmups",
                    "0",
                    "--repetitions",
                    "1",
                    "--feature-workloads-only",
                    "--json-output",
                    str(output_path),
                ],
                cwd=ROOT,
                capture_output=True,
                text=True,
                check=False,
                timeout=30,
            )

            self.assertEqual(completed.returncode, 0, completed.stderr)
            result = json.loads(output_path.read_text(encoding="utf-8"))

        outcomes = {item["case_id"]: item for item in result["outcomes"]}
        self.assertEqual(expected, outcomes.keys())
        for case_id in expected:
            self.assertEqual(outcomes[case_id]["status"], "measured", case_id)
            self.assertEqual(outcomes[case_id]["config"]["repetitions"], 1)

    def test_cli_measures_session_open_at_high_occupancy(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output_path = Path(directory) / "connect.json"
            completed = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "tools.connect_performance_benchmark",
                    "--operation",
                    "open_or_resume + assert_current (900 active sessions)",
                    "--warmups",
                    "0",
                    "--repetitions",
                    "1",
                    "--json-output",
                    str(output_path),
                ],
                cwd=ROOT,
                capture_output=True,
                text=True,
                check=False,
                timeout=10,
            )

            self.assertEqual(completed.returncode, 0, completed.stderr)
            outcome = json.loads(output_path.read_text(encoding="utf-8"))["outcomes"][0]

        self.assertEqual(outcome["fixture"]["active_sessions_before"], 900)
        self.assertEqual(outcome["fixture"]["max_sessions"], 1024)
        self.assertEqual(outcome["config"]["repetitions"], 1)

    def test_high_occupancy_setup_is_repeated_for_every_sample_and_allocation(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output_path = Path(directory) / "connect.json"
            completed = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "tools.connect_performance_benchmark",
                    "--operation",
                    "open_or_resume + assert_current (900 active sessions)",
                    "--warmups",
                    "0",
                    "--repetitions",
                    "125",
                    "--track-allocations",
                    "--json-output",
                    str(output_path),
                ],
                cwd=ROOT,
                capture_output=True,
                text=True,
                check=False,
                timeout=30,
            )

            self.assertEqual(completed.returncode, 0, completed.stderr)
            outcome = json.loads(output_path.read_text(encoding="utf-8"))["outcomes"][0]

        self.assertEqual(outcome["config"]["repetitions"], 125)
        self.assertIsNotNone(outcome["result"]["peak_bytes"])

    def test_module_cli_selects_operation_and_emits_shared_result(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output_path = Path(directory) / "connect.json"
            try:
                completed = subprocess.run(
                    [
                        sys.executable,
                        "-m",
                        "tools.connect_performance_benchmark",
                        "--operation",
                        "identity fingerprint",
                        "--warmups",
                        "0",
                        "--repetitions",
                        "1",
                        "--json-output",
                        str(output_path),
                    ],
                    cwd=ROOT,
                    capture_output=True,
                    text=True,
                    check=False,
                    timeout=5,
                )
            except subprocess.TimeoutExpired:
                self.fail(
                    "targeted benchmark ignored --operation and ran the full suite"
                )

            self.assertEqual(completed.returncode, 0, completed.stderr)
            result = json.loads(output_path.read_text(encoding="utf-8"))

        self.assertEqual(result["metadata"]["suite"], "connect")
        self.assertEqual(len(result["outcomes"]), 1)
        outcome = result["outcomes"][0]
        self.assertEqual(outcome["operation"], "identity fingerprint")
        self.assertEqual(outcome["config"]["repetitions"], 1)
        self.assertIn("expra_connect.identity", result["metadata"]["modules"])
        self.assertIn("p50=", completed.stdout)

    def test_stateful_operation_rejects_batched_execution(self) -> None:
        try:
            completed = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "tools.connect_performance_benchmark",
                    "--operation",
                    "replay cache check_and_record",
                    "--batch-count",
                    "2",
                ],
                cwd=ROOT,
                capture_output=True,
                text=True,
                check=False,
                timeout=5,
            )
        except subprocess.TimeoutExpired:
            self.fail("targeted benchmark ignored --batch-count and ran the full suite")

        self.assertNotEqual(completed.returncode, 0)
        self.assertIn("cannot be batched", completed.stderr)

    def test_legacy_flat_package_layout_marks_missing_yaml_store_unsupported(
        self,
    ) -> None:
        package_root = Path("/tmp/opencode/bench-comparison-2.2.0.1/1.8.0.0")
        environment = os.environ.copy()
        environment["PYTHONPATH"] = str(package_root)
        with tempfile.TemporaryDirectory() as directory:
            output_path = Path(directory) / "connect-1.8.0.0.json"
            completed = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "tools.connect_performance_benchmark",
                    "--warmups",
                    "0",
                    "--repetitions",
                    "1",
                    "--json-output",
                    str(output_path),
                ],
                cwd=ROOT,
                env=environment,
                capture_output=True,
                text=True,
                check=False,
                timeout=30,
            )

            self.assertEqual(completed.returncode, 0, completed.stderr)
            result = json.loads(output_path.read_text(encoding="utf-8"))

        self.assertEqual(result["metadata"]["package_version"], "1.8.0.0")
        self.assertTrue(
            all(
                str(package_root) in module_path
                for module_path in result["metadata"]["modules"].values()
            )
        )
        yaml_outcomes = [
            outcome
            for outcome in result["outcomes"]
            if outcome["operation"].startswith("yaml ")
        ]
        self.assertEqual(len(yaml_outcomes), 2)
        self.assertTrue(
            all(outcome["status"] == "unavailable" for outcome in yaml_outcomes)
        )


if __name__ == "__main__":
    unittest.main()

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

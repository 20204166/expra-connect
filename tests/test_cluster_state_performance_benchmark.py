from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class ClusterStatePerformanceBenchmarkTests(unittest.TestCase):
    def test_yaml_operation_rejects_sizes_above_configured_limit(self) -> None:
        completed = subprocess.run(
            [
                sys.executable,
                "-m",
                "scripts.benchmark_cluster_state",
                "--sizes",
                "30001",
                "--operation",
                "YAML parse",
            ],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
            timeout=10,
        )
        self.assertEqual(completed.returncode, 2)
        self.assertIn(
            "selected YAML operation exceeds --yaml-max-size", completed.stderr
        )

    def test_cli_measures_role_authorization_at_each_fixture_size(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output_path = Path(directory) / "cluster-state.json"
            completed = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "scripts.benchmark_cluster_state",
                    "--sizes",
                    "1000",
                    "30000",
                    "100000",
                    "200000",
                    "--operation",
                    "role authorization",
                    "--warmups",
                    "0",
                    "--repetitions",
                    "1",
                    "--skip-allocation-tracking",
                    "--json-output",
                    str(output_path),
                ],
                cwd=ROOT,
                capture_output=True,
                text=True,
                check=False,
                timeout=120,
            )

            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertEqual(
                [
                    line
                    for line in completed.stdout.splitlines()
                    if line.startswith("correctness=")
                ],
                [
                    f"correctness=passed size={size} checks=12"
                    for size in (1000, 30000, 100000, 200000)
                ],
            )
            run = json.loads(output_path.read_text(encoding="utf-8"))

        outcomes = run["outcomes"]
        self.assertEqual(
            [outcome["case_id"] for outcome in outcomes],
            [
                f"{size}:{label}"
                for size in (1000, 30000, 100000, 200000)
                for label in (
                    "role authorization",
                    "role authorization worker denied",
                    "role authorization subcoordinator grant",
                    "role authorization subcoordinator missing grant",
                    "role authorization subcoordinator expired grant",
                    "role authorization paused source",
                    "role authorization revoked destination",
                    "role authorization invalid clock",
                )
            ],
        )
        self.assertEqual(
            [item["fixture"]["assignments"] for item in outcomes],
            [size for size in (1000, 30000, 100000, 200000) for _ in range(8)],
        )
        self.assertTrue(all(item["status"] == "measured" for item in outcomes))


if __name__ == "__main__":
    unittest.main()

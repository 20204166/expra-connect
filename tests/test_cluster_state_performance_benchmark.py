from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class ClusterStatePerformanceBenchmarkTests(unittest.TestCase):
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
                timeout=30,
            )

            self.assertEqual(completed.returncode, 0, completed.stderr)
            run = json.loads(output_path.read_text(encoding="utf-8"))

        outcomes = run["outcomes"]
        self.assertEqual(
            [outcome["case_id"] for outcome in outcomes],
            [
                "1000:role authorization",
                "30000:role authorization",
                "100000:role authorization",
            ],
        )
        self.assertEqual(
            [item["fixture"]["assignments"] for item in outcomes], [1000, 30000, 100000]
        )
        self.assertTrue(all(item["status"] == "measured" for item in outcomes))


if __name__ == "__main__":
    unittest.main()

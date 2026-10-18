import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from expra_connect.cluster import ClusterRole, RoleState
from scripts import benchmark_cluster_state
from scripts.benchmark_support import MeasurementConfig, measure

ROOT = Path(__file__).resolve().parents[1]


class ClusterStateBenchmarkTests(unittest.TestCase):
    def test_operation_selection_preserves_order_and_rejects_unknown_names(
        self,
    ) -> None:
        operations = [
            ("role encode", lambda: "role"),
            ("cluster round-trip", lambda: "cluster"),
        ]

        self.assertEqual(
            benchmark_cluster_state._select_operations(
                operations, "cluster round-trip"
            ),
            [operations[1]],
        )
        with self.assertRaisesRegex(ValueError, "unknown benchmark operation"):
            benchmark_cluster_state._select_operations(operations, "not an operation")

    def test_records_fixture_matches_staged_node_and_grant_counts(self) -> None:
        assignments, grants, epoch = benchmark_cluster_state._records(3)

        self.assertEqual(len(assignments), 3)
        self.assertEqual(len(grants), 3)
        self.assertIn(ClusterRole.COORDINATOR, assignments[0].roles)
        self.assertEqual(epoch.coordinator_id, assignments[0].node_id)

    def test_measure_batches_each_sample_and_warmup(self) -> None:
        calls = 0

        def operation() -> None:
            nonlocal calls
            calls += 1

        measure(
            operation,
            MeasurementConfig(
                warmups=1, repetitions=2, batch_count=3, track_allocations=False
            ),
        )

        self.assertEqual(calls, 9)

    def test_cluster_document_matches_staged_persisted_shape(self) -> None:
        assignments, grants, epoch = benchmark_cluster_state._records(2)

        document = benchmark_cluster_state._cluster_document(
            RoleState(assignments, epoch, capability_grants=grants)
        )

        self.assertEqual(document["schema_version"], 2)
        self.assertEqual(document["local_node_id"], "node-0")
        self.assertEqual(document["assignments"][0]["roles"], ["coordinator", "worker"])
        self.assertNotIn("role", document["assignments"][0])
        self.assertEqual(document["capability_grants"][0]["target"], "target-0")
        self.assertEqual(document["join_admissions"], [])

    def test_module_cli_emits_shared_structured_results(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output_path = Path(directory) / "cluster.json"
            completed = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "scripts.benchmark_cluster_state",
                    "--sizes",
                    "1",
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
            )

            self.assertEqual(completed.returncode, 0, completed.stderr)
            result = json.loads(output_path.read_text(encoding="utf-8"))

        self.assertEqual(result["metadata"]["suite"], "cluster_state")
        self.assertIn("expra_connect.cluster.state", result["metadata"]["modules"])
        self.assertTrue(result["outcomes"])
        self.assertTrue(all(outcome["size"] == 1 for outcome in result["outcomes"]))
        self.assertTrue(
            all(outcome["payload_bytes"] > 0 for outcome in result["outcomes"])
        )
        self.assertIn("p50=", completed.stdout)

    def test_legacy_cluster_package_layout_emits_shared_results(self) -> None:
        package_root = Path("/tmp/opencode/bench-comparison-2.2.0.1/1.8.0.0")
        environment = os.environ.copy()
        environment["PYTHONPATH"] = str(package_root)
        with tempfile.TemporaryDirectory() as directory:
            output_path = Path(directory) / "cluster-1.8.0.0.json"
            completed = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "scripts.benchmark_cluster_state",
                    "--sizes",
                    "1",
                    "--operation",
                    "role encode",
                    "--warmups",
                    "0",
                    "--repetitions",
                    "1",
                    "--skip-allocation-tracking",
                    "--json-output",
                    str(output_path),
                ],
                cwd=ROOT,
                env=environment,
                capture_output=True,
                text=True,
                check=False,
                timeout=15,
            )

            self.assertEqual(completed.returncode, 0, completed.stderr)
            result = json.loads(output_path.read_text(encoding="utf-8"))

        self.assertEqual(result["metadata"]["package_version"], "1.8.0.0")
        self.assertEqual(len(result["outcomes"]), 1)


if __name__ == "__main__":
    unittest.main()

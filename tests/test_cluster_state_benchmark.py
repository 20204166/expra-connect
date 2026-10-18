import unittest

from expra_connect.cluster import ClusterRole, RoleState
from scripts import benchmark_cluster_state


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

        benchmark_cluster_state._measure(
            operation,
            warmups=1,
            repetitions=2,
            batch_count=3,
            track_allocations=False,
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


if __name__ == "__main__":
    unittest.main()

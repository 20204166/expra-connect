from __future__ import annotations

import unittest

from expra_connect.cluster import Cluster, ClusterRole, ClusterState
from expra_connect.cluster.roles import RoleState
from expra_connect.cluster.state import role_state_from_dict, role_state_to_dict
from expra_connect.identity import NodeId
from scripts.benchmark_cluster_state import _records

SIZES = (1_000, 30_000, 100_000, 200_000)


class ClusterStateScaleCorrectnessTests(unittest.TestCase):
    def test_role_and_cluster_round_trips_and_hydration_at_each_scale(self) -> None:
        for size in SIZES:
            with self.subTest(size=size):
                assignments, grants, epoch = _records(size)
                role_state = RoleState(assignments, epoch, frozenset({22}), grants)
                role_document = role_state_to_dict(role_state)
                decoded_role = role_state_from_dict(role_document)
                self.assertEqual(role_state_to_dict(decoded_role), role_document)
                self.assertEqual(decoded_role.epoch, epoch)
                self.assertEqual(decoded_role.promotion_epochs, frozenset({22}))
                self.assertEqual(decoded_role.capability_grants, grants)

                state = ClusterState(
                    local_node_id="node-0",
                    cluster_id="benchmark-cluster",
                    role_assignments=assignments,
                    coordinator_epoch=epoch,
                    promotion_epochs=frozenset({22}),
                    capability_grants=grants,
                )
                document = state.to_dict()
                restored = ClusterState.from_dict(document)
                self.assertEqual(restored.to_dict(), document)
                self.assertEqual(restored.role_assignments, assignments)
                self.assertEqual(restored.coordinator_epoch, epoch)
                self.assertEqual(restored.capability_grants, grants)

                hydrated = Cluster.from_state(
                    NodeId("node-0"), restored, clock=lambda: 110.0
                )
                self.assertEqual(hydrated.local_id, NodeId("node-0"))
                self.assertEqual(hydrated.epoch, epoch)
                self.assertEqual(len(hydrated.assignments), size)
                with self.assertRaisesRegex(ValueError, "another local node"):
                    Cluster.from_state(NodeId("other-node"), restored)

    def test_late_duplicate_assignment_and_coordinator_are_rejected(self) -> None:
        assignments, grants, epoch = _records(200_000)
        document = role_state_to_dict(
            RoleState(assignments, epoch, capability_grants=grants)
        )
        duplicate_assignment = dict(document)
        duplicate_assignment["assignments"] = [
            *document["assignments"],
            document["assignments"][-1],
        ]
        with self.assertRaisesRegex(ValueError, "duplicate role assignment"):
            role_state_from_dict(duplicate_assignment)

        duplicate_coordinator = dict(document)
        duplicate_coordinator["assignments"] = [
            *document["assignments"],
            {"node_id": "late-coordinator", "roles": [ClusterRole.COORDINATOR.value]},
        ]
        with self.assertRaisesRegex(ValueError, "only one active Coordinator"):
            role_state_from_dict(duplicate_coordinator)


if __name__ == "__main__":
    unittest.main()

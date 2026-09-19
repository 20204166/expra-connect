import tempfile
import unittest
from pathlib import Path

from expra_connect.cluster import Cluster, ClusterRole
from expra_connect.identity import NodeId
from expra_connect.persistence import JsonStateStore


class ClusterPersistenceTests(unittest.TestCase):
    def test_persisted_membership_keeps_role_and_coordinator_identity(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            cluster = Cluster(NodeId("coord"))
            cluster.assign(NodeId("worker"), ClusterRole.WORKER)
            cluster.save(JsonStateStore(Path(directory) / "cluster.json"))
            state = JsonStateStore(Path(directory) / "cluster.json").load()
            self.assertEqual(state["local_id"], "coord")
            self.assertEqual(state["assignments"][0]["role"], "coordinator")

    def test_cluster_load_restores_epoch_and_rejects_missing_local_membership(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "cluster.json"
            cluster = Cluster(NodeId("worker"))
            cluster.save(JsonStateStore(path))
            restored = Cluster.load(NodeId("worker"), JsonStateStore(path))
            self.assertEqual(restored.cluster_id, cluster.cluster_id)
            self.assertEqual(restored.epoch, cluster.epoch)
            JsonStateStore(path).save(
                {
                    "cluster_id": "c",
                    "epoch": 1,
                    "coordinator_id": "coord",
                    "fencing_token": "token",
                    "assignments": [],
                }
            )
            with self.assertRaises(ValueError):
                Cluster.load(NodeId("worker"), JsonStateStore(path))
            JsonStateStore(path).save(cluster.to_dict())
            with self.assertRaises(ValueError):
                Cluster.load(NodeId("other"), JsonStateStore(path))

    def test_two_active_coordinators_are_rejected(self) -> None:
        cluster = Cluster(NodeId("coord"))
        with self.assertRaises(ValueError):
            cluster.assign(NodeId("other"), ClusterRole.COORDINATOR)

    def test_consumed_invites_remain_consumed_after_reload(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "cluster.json"
            coordinator = Cluster(NodeId("coord"))
            invite = coordinator.create_invite(NodeId("worker"), now=10.0)
            worker = Cluster(NodeId("worker"))
            worker.join(invite, coordinator, now=10.0)
            coordinator.save(JsonStateStore(path))
            restored = Cluster.load(NodeId("coord"), JsonStateStore(path))
            with self.assertRaises(ValueError):
                worker.join(invite, restored, now=11.0)

    def test_invite_bound_to_old_epoch_cannot_join_after_epoch_change(self) -> None:
        coordinator = Cluster(NodeId("coord"))
        invite = coordinator.create_invite(NodeId("worker"), now=10.0)
        coordinator.epoch = coordinator.epoch.__class__(
            coordinator.epoch.epoch + 1,
            coordinator.epoch.coordinator_id,
            coordinator.epoch.fencing_token,
        )
        with self.assertRaises(ValueError):
            Cluster(NodeId("worker")).join(invite, coordinator, now=11.0)

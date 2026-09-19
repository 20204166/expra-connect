import unittest

from expra_connect.cluster import Cluster, ClusterRole
from expra_connect.identity import NodeId


class ClusterTests(unittest.TestCase):
    def test_join_assigns_worker_and_coordinator_identity(self) -> None:
        coordinator = Cluster(NodeId("coord"))
        invite = coordinator.create_invite(NodeId("worker"), now=100.0)
        worker = Cluster(NodeId("worker"))
        worker.join(invite, coordinator, now=100.0)
        self.assertEqual(worker.cluster_id, coordinator.cluster_id)
        self.assertEqual(worker.coordinator_id, NodeId("coord"))
        self.assertEqual(worker.local_role, ClusterRole.WORKER)
        self.assertTrue(coordinator.is_member(NodeId("worker")))

    def test_offline_does_not_remove_membership(self) -> None:
        cluster = Cluster(NodeId("coord"))
        cluster.assign(NodeId("worker"), ClusterRole.WORKER)
        cluster.set_online(NodeId("worker"), False)
        self.assertTrue(cluster.is_member(NodeId("worker")))
        self.assertFalse(cluster.online(NodeId("worker")))

    def test_stale_fencing_authority_is_rejected_and_revoke_is_membership_only(
        self,
    ) -> None:
        cluster = Cluster(NodeId("coord"))
        cluster.assign(NodeId("worker"), ClusterRole.WORKER)
        with self.assertRaises(PermissionError):
            cluster.authorize(
                cluster_id=cluster.cluster_id, epoch=0, fencing_token="old"
            )
        cluster.revoke(NodeId("worker"))
        self.assertFalse(cluster.is_member(NodeId("worker")))

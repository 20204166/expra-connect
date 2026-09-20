import tempfile
import unittest
from pathlib import Path
from typing import cast

from expra_connect.cluster import Cluster, ClusterDataError, ClusterRole, ClusterState
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
            worker.join(invite, coordinator, pairing=lambda _peer: True, now=10.0)
            coordinator.save(JsonStateStore(path))
            restored = Cluster.load(NodeId("coord"), JsonStateStore(path))
            with self.assertRaises(ValueError):
                worker.join(invite, restored, pairing=lambda _peer: True, now=11.0)

    def test_invite_bound_to_old_epoch_cannot_join_after_epoch_change(self) -> None:
        coordinator = Cluster(NodeId("coord"))
        invite = coordinator.create_invite(NodeId("worker"), now=10.0)
        coordinator.epoch = coordinator.epoch.__class__(
            coordinator.epoch.epoch + 1,
            coordinator.epoch.coordinator_id,
            coordinator.epoch.fencing_token,
        )
        with self.assertRaises(ValueError):
            Cluster(NodeId("worker")).join(
                invite, coordinator, pairing=lambda _peer: True, now=11.0
            )

    def test_active_invite_is_restored_without_persisting_raw_token(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "cluster.json"
            coordinator = Cluster(NodeId("coord"))
            invite = coordinator.create_invite(NodeId("worker"), now=10.0)
            coordinator.save(JsonStateStore(path))
            document = path.read_text()
            self.assertNotIn(invite.token, document)
            restored = Cluster.load(NodeId("coord"), JsonStateStore(path))
            restored.consume_invite(invite.token, NodeId("worker"), now=11.0)
            with self.assertRaises(ValueError):
                restored.consume_invite(invite.token, NodeId("worker"), now=12.0)

    def test_legacy_used_tokens_are_migrated_to_hashes(self) -> None:
        token = "legacy-invite"
        state = ClusterState.from_dict(
            {
                "schema_version": 2,
                "local_node_id": "coord",
                "cluster_id": "cluster",
                "assignments": [{"node_id": "coord", "roles": ["coordinator"]}],
                "epoch": {
                    "epoch": 1,
                    "coordinator_id": "coord",
                    "fencing_token": "fence",
                    "issued_at": 0.0,
                    "lease_expires_at": 0.0,
                },
                "used_invites": [token],
            }
        )
        self.assertNotIn(token, state.to_dict()["used_invites"])
        self.assertEqual(len(state.to_dict()["used_invites"][0]), 64)

    def test_malformed_cluster_numbers_and_booleans_are_rejected(self) -> None:
        base = {
            "schema_version": 2,
            "local_node_id": "coord",
            "cluster_id": "cluster",
            "assignments": [{"node_id": "coord", "roles": ["coordinator"]}],
            "epoch": {
                "epoch": 1,
                "coordinator_id": "coord",
                "fencing_token": "fence",
                "issued_at": 0.0,
                "lease_expires_at": 0.0,
            },
        }
        for field, value in (
            ("epoch", True),
            ("issued_at", float("nan")),
            ("lease_expires_at", -1.0),
        ):
            malformed = dict(base)
            malformed["epoch"] = dict(cast(dict[str, object], base["epoch"]))
            epoch = cast(dict[str, object], malformed["epoch"])
            epoch[field] = value
            with self.assertRaises((ValueError, ClusterDataError)):
                ClusterState.from_dict(malformed)

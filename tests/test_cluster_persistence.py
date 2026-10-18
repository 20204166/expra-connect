import tempfile
import unittest
from pathlib import Path
from typing import cast

from expra_connect.cluster import (
    CapabilityGrant,
    Cluster,
    ClusterDataError,
    ClusterRole,
    ClusterState,
    CoordinatorEpoch,
    InviteRecord,
    RoleAssignment,
)
from expra_connect.cluster.persistence import ClusterStore
from expra_connect.data.permissions import NodePermission
from expra_connect.identity import NodeId
from expra_connect.persistence import YamlStateStore


class ClusterPersistenceTests(unittest.TestCase):
    def test_cluster_state_round_trip_preserves_persisted_fields_and_hides_tokens(
        self,
    ) -> None:
        active_invite = InviteRecord(
            "a" * 64,
            "worker",
            20.0,
            token="one-time-secret",
            cluster_id="cluster",
            coordinator_id="coord",
            epoch=4,
            fencing_token="fence",
        )
        admission = InviteRecord(
            "b" * 64,
            "worker",
            20.0,
            cluster_id="cluster",
            coordinator_id="coord",
            epoch=4,
            fencing_token="fence",
        )
        grant = CapabilityGrant(
            NodeId("sub"),
            NodeId("worker"),
            frozenset({NodePermission.READ_STATE}),
            1.0,
            10.0,
        )
        state = ClusterState(
            local_node_id="coord",
            cluster_id="cluster",
            role_assignments=(
                RoleAssignment(frozenset({ClusterRole.COORDINATOR}), NodeId("coord")),
                RoleAssignment(frozenset({ClusterRole.SUBCOORDINATOR}), NodeId("sub")),
                RoleAssignment(frozenset({ClusterRole.WORKER}), NodeId("worker")),
            ),
            coordinator_epoch=CoordinatorEpoch(4, NodeId("coord"), "fence", 1, 10),
            active_invites=(active_invite,),
            join_admissions=(admission,),
            used_invites=frozenset({"c" * 64}),
            promotion_epochs=frozenset({2, 3}),
            capability_grants=(grant,),
        )

        encoded = state.to_dict()
        restored = ClusterState.from_dict(encoded)

        self.assertEqual(encoded["schema_version"], 2)
        self.assertEqual(encoded["local_node_id"], "coord")
        self.assertEqual(encoded["local_id"], "coord")
        self.assertEqual(encoded["assignments"][0]["role"], "coordinator")
        self.assertNotIn("one-time-secret", repr(encoded))
        self.assertEqual(restored.local_node_id, state.local_node_id)
        self.assertEqual(restored.cluster_id, state.cluster_id)
        self.assertEqual(restored.role_assignments, state.role_assignments)
        self.assertEqual(restored.coordinator_epoch, state.coordinator_epoch)
        self.assertEqual(
            restored.active_invites,
            (
                InviteRecord(
                    active_invite.token_hash,
                    active_invite.target_node_id,
                    active_invite.expires_at,
                    cluster_id=active_invite.cluster_id,
                    coordinator_id=active_invite.coordinator_id,
                    epoch=active_invite.epoch,
                    fencing_token=active_invite.fencing_token,
                ),
            ),
        )
        self.assertEqual(restored.join_admissions, state.join_admissions)
        self.assertEqual(restored.used_invites, state.used_invites)
        self.assertEqual(restored.promotion_epochs, state.promotion_epochs)
        self.assertEqual(restored.capability_grants, state.capability_grants)

    def test_cluster_from_state_restores_runtime_owned_collections(self) -> None:
        cluster = Cluster(NodeId("coord"))
        cluster.assign(NodeId("worker"), ClusterRole.WORKER)
        state = ClusterState.from_dict(cluster.to_dict())

        restored = Cluster.from_state(NodeId("coord"), state)

        self.assertEqual(restored.cluster_id, cluster.cluster_id)
        self.assertEqual(restored.assignments, cluster.assignments)
        self.assertTrue(restored.online(NodeId("coord")))

    def test_cluster_facade_round_trips_through_cluster_store(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = ClusterStore(Path(directory) / "cluster.yaml")
            cluster = Cluster(NodeId("coord"))
            cluster.assign(NodeId("worker"), ClusterRole.WORKER)

            cluster.save(store)
            restored = Cluster.load(NodeId("coord"), store)

            self.assertEqual(restored.cluster_id, cluster.cluster_id)
            self.assertEqual(restored.assignments, cluster.assignments)

    def test_persisted_membership_keeps_role_and_coordinator_identity(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            cluster = Cluster(NodeId("coord"))
            cluster.assign(NodeId("worker"), ClusterRole.WORKER)
            cluster.save(YamlStateStore(Path(directory) / "cluster.yaml"))
            state = YamlStateStore(Path(directory) / "cluster.yaml").load()
            self.assertEqual(state["local_id"], "coord")
            self.assertEqual(state["epoch"]["coordinator_id"], "coord")
            assignments = {item["node_id"]: item for item in state["assignments"]}
            self.assertEqual(assignments["coord"]["role"], "coordinator")
            self.assertEqual(assignments["worker"]["role"], "worker")

    def test_cluster_load_restores_epoch_and_rejects_missing_local_membership(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "cluster.yaml"
            cluster = Cluster(NodeId("worker"))
            cluster.save(YamlStateStore(path))
            restored = Cluster.load(NodeId("worker"), YamlStateStore(path))
            self.assertEqual(restored.cluster_id, cluster.cluster_id)
            self.assertEqual(restored.epoch, cluster.epoch)
            YamlStateStore(path).save(
                {
                    "cluster_id": "c",
                    "epoch": 1,
                    "coordinator_id": "coord",
                    "fencing_token": "token",
                    "assignments": [],
                }
            )
            with self.assertRaises(ValueError):
                Cluster.load(NodeId("worker"), YamlStateStore(path))
            YamlStateStore(path).save(cluster.to_dict())
            with self.assertRaises(ValueError):
                Cluster.load(NodeId("other"), YamlStateStore(path))

    def test_two_active_coordinators_are_rejected(self) -> None:
        cluster = Cluster(NodeId("coord"))
        with self.assertRaises(ValueError):
            cluster.assign(NodeId("other"), ClusterRole.COORDINATOR)

    def test_consumed_invites_remain_consumed_after_reload(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "cluster.yaml"
            coordinator = Cluster(NodeId("coord"))
            invite = coordinator.create_invite(NodeId("worker"), now=10.0)
            worker = Cluster(NodeId("worker"))
            worker.join(invite, coordinator, pairing=lambda _peer: True, now=10.0)
            coordinator.save(YamlStateStore(path))
            restored = Cluster.load(NodeId("coord"), YamlStateStore(path))
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
            path = Path(directory) / "cluster.yaml"
            coordinator = Cluster(NodeId("coord"))
            invite = coordinator.create_invite(NodeId("worker"), now=10.0)
            coordinator.save(YamlStateStore(path))
            document = path.read_text()
            self.assertNotIn(invite.token, document)
            restored = Cluster.load(NodeId("coord"), YamlStateStore(path))
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

    def test_cluster_store_auto_migrates_json_to_yaml_on_first_load(self) -> None:
        from expra_connect.cluster.persistence import ClusterStore

        with tempfile.TemporaryDirectory() as directory:
            yaml_path = Path(directory) / "cluster.yaml"
            json_path = Path(directory) / "cluster.json"
            coordinator = Cluster(NodeId("coord"))
            coordinator.save(YamlStateStore(json_path.with_suffix(".yaml")))
            raw = yaml_path.read_text()
            json_path.write_text(
                __import__("json").dumps(__import__("yaml").safe_load(raw)),
                encoding="utf-8",
            )
            yaml_path.unlink()
            store = ClusterStore(yaml_path)
            restored = store.load()
            self.assertEqual(restored.local_node_id, "coord")
            self.assertTrue(yaml_path.exists(), "yaml not created after migration")
            self.assertTrue(json_path.exists(), "legacy JSON was deleted")

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

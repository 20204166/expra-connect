import unittest

from expra_connect.cluster import (
    Cluster,
    ClusterRole,
    ClusterState,
    CoordinatorEpoch,
    FailoverCoordinator,
    RoleAssignment,
    RoleState,
    promote_subcoordinator,
    role_state_from_dict,
    role_state_to_dict,
)
from expra_connect.identity import NodeId
from expra_connect.models import NodePermission


class CanonicalClusterTests(unittest.TestCase):
    def test_join_admission_is_retryable_after_first_commit(self) -> None:
        coordinator = Cluster(NodeId("coord"), clock=lambda: 10.0)
        worker = Cluster(NodeId("worker"), clock=lambda: 10.0)
        invite = coordinator.create_invite(NodeId("worker"), now=10.0)

        worker.join(
            invite,
            coordinator,
            pairing=lambda _peer: True,
            now=10.0,
        )
        retry = coordinator.consume_invite(
            invite.token, NodeId("worker"), now=400.0, allow_retry=True
        )

        self.assertEqual(retry.token_hash, invite.token_hash)
        self.assertEqual(len(coordinator._join_admissions), 1)

    def test_join_admission_survives_persistence_round_trip(self) -> None:
        coordinator = Cluster(NodeId("coord"), clock=lambda: 10.0)
        worker = Cluster(NodeId("worker"), clock=lambda: 10.0)
        invite = coordinator.create_invite(NodeId("worker"), now=10.0)
        worker.join(invite, coordinator, pairing=lambda _peer: True, now=10.0)

        restored = ClusterState.from_dict(coordinator.to_dict())
        self.assertEqual(len(restored.join_admissions), 1)
        self.assertNotIn(invite.token, repr(restored.to_dict()))

    def test_invite_token_is_not_in_persisted_document(self) -> None:
        state = ClusterState.create_local(local_node_id="coord", now=10.0)
        invite = state.create_invite(target_node_id="worker", now=10.0)
        document = state.to_dict()
        self.assertNotIn(invite.token, repr(document))
        self.assertEqual(len(document["active_invites"]), 1)

    def test_promotion_is_allowed_at_exact_lease_boundary(self) -> None:
        state = RoleState(
            assignments=(
                RoleAssignment(frozenset({ClusterRole.COORDINATOR}), NodeId("coord")),
                RoleAssignment(frozenset({ClusterRole.SUBCOORDINATOR}), NodeId("sub")),
            ),
            epoch=CoordinatorEpoch(1, NodeId("coord"), "fence", 0.0, 100.0),
        )
        self.assertEqual(promote_subcoordinator(state, now=100.0).epoch.epoch, 2)

    def test_failover_does_not_publish_unpersisted_state(self) -> None:
        state = RoleState(
            assignments=(
                RoleAssignment(frozenset({ClusterRole.COORDINATOR}), NodeId("coord")),
                RoleAssignment(frozenset({ClusterRole.SUBCOORDINATOR}), NodeId("sub")),
            ),
            epoch=CoordinatorEpoch(1, NodeId("coord"), "fence", 0.0, 100.0),
        )
        manager = FailoverCoordinator(
            state, persist=lambda _state: (_ for _ in ()).throw(OSError("disk"))
        )
        with self.assertRaises(OSError):
            manager.promote_if_due(now=100.0)
        epoch = manager.state.epoch
        assert epoch is not None
        self.assertEqual(epoch.coordinator_id, NodeId("coord"))

    def test_capability_grants_hydrate_from_persisted_role_state(self) -> None:
        from expra_connect.cluster.roles import RoleState

        state = RoleState(
            assignments=(
                RoleAssignment(frozenset({ClusterRole.COORDINATOR}), NodeId("coord")),
                RoleAssignment(frozenset({ClusterRole.SUBCOORDINATOR}), NodeId("sub")),
                RoleAssignment(frozenset({ClusterRole.WORKER}), NodeId("worker")),
            ),
            epoch=CoordinatorEpoch(1, NodeId("coord"), "fence", 0.0, 100.0),
        )
        coordinator = state.assignment_for(NodeId("coord"))
        assert coordinator is not None
        granted = state.grant_capabilities(
            actor=coordinator,
            subject=NodeId("sub"),
            target=NodeId("worker"),
            permissions=frozenset({NodePermission.READ_STATE}),
            now=1.0,
            expires_at=10.0,
        )
        restored = role_state_from_dict(role_state_to_dict(granted))
        self.assertEqual(
            restored.capability_grant(NodeId("sub"), NodeId("worker")),
            granted.capability_grant(NodeId("sub"), NodeId("worker")),
        )

    def test_duplicate_active_subcoordinators_fail_closed_on_load(self) -> None:
        with self.assertRaises(ValueError):
            role_state_from_dict(
                {
                    "assignments": [
                        {"node_id": "a", "roles": ["subcoordinator"]},
                        {"node_id": "b", "roles": ["subcoordinator"]},
                    ]
                }
            )

    def test_paused_local_coordinator_cannot_create_invite(self) -> None:
        state = ClusterState.create_local(local_node_id="coord", now=10.0)
        state.role_assignments = (
            RoleAssignment(
                frozenset({ClusterRole.COORDINATOR}),
                NodeId("coord"),
                paused=True,
            ),
        )
        with self.assertRaises(PermissionError):
            state.create_invite(target_node_id="worker", now=10.0)

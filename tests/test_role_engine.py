import unittest

from expra_connect.cluster.state import role_state_to_dict as encode_role_state
from expra_connect.identity import NodeId
from expra_connect.models import NodePermission
from expra_connect.role_engine import (
    ClusterRole,
    CoordinatorEpoch,
    FencingError,
    RoleAssignment,
    RoleAuthorizationError,
    RoleState,
    promote_subcoordinator,
    rejoin_as_worker,
    renew_lease,
    role_state_from_dict,
    role_state_to_dict,
)


class RoleEngineTests(unittest.TestCase):
    def setUp(self) -> None:
        self.coordinator = RoleAssignment(
            frozenset({ClusterRole.COORDINATOR}), NodeId("coord")
        )
        self.worker = RoleAssignment(frozenset({ClusterRole.WORKER}), NodeId("worker"))
        self.sub = RoleAssignment(
            frozenset({ClusterRole.SUBCOORDINATOR}), NodeId("sub")
        )

    def test_coordinator_is_also_worker_and_worker_cannot_control(self) -> None:
        self.assertIn(ClusterRole.WORKER, self.coordinator.roles)
        with self.assertRaises(RoleAuthorizationError):
            RoleState().assign(
                actor=self.worker,
                target=NodeId("peer"),
                roles=frozenset({ClusterRole.WORKER}),
            )

    def test_only_one_subcoordinator_is_allowed(self) -> None:
        state = RoleState(assignments=(self.coordinator, self.sub))
        with self.assertRaises(RoleAuthorizationError):
            state.assign(
                actor=self.coordinator,
                target=NodeId("other"),
                roles=frozenset({ClusterRole.SUBCOORDINATOR}),
            )

    def test_expired_epoch_promotes_subcoordinator(self) -> None:
        state = RoleState(
            assignments=(self.coordinator, self.sub),
            epoch=CoordinatorEpoch(7, NodeId("coord"), "token", 0.0, 100.0),
        )
        decision = promote_subcoordinator(state, now=120.1)
        self.assertEqual(decision.epoch.epoch, 8)
        self.assertEqual(decision.epoch.coordinator_id, NodeId("sub"))

    def test_promotion_before_expiry_and_stale_renewal_are_rejected(self) -> None:
        epoch = CoordinatorEpoch(1, NodeId("coord"), "token", 0.0, 100.0)
        state = RoleState(assignments=(self.coordinator, self.sub), epoch=epoch)
        with self.assertRaises(FencingError):
            promote_subcoordinator(state, now=99.0)
        with self.assertRaises(FencingError):
            renew_lease(
                epoch,
                coordinator_id=NodeId("coord"),
                fencing_token="old",
                now=10.0,
            )

    def test_returning_coordinator_is_fenced_to_worker(self) -> None:
        state = RoleState(
            assignments=(self.coordinator, self.worker),
            epoch=CoordinatorEpoch(3, NodeId("new"), "token", 0.0, 100.0),
        )
        updated = rejoin_as_worker(state, node_id=NodeId("coord"), current_epoch=3)
        assignment = updated.assignment_for(NodeId("coord"))
        if assignment is None:
            self.fail("returning coordinator assignment was lost")
        self.assertEqual(assignment.roles, frozenset({ClusterRole.WORKER}))

    def test_role_state_round_trip_normalizes_runtime_job_state(self) -> None:
        state = RoleState(
            assignments=(
                RoleAssignment(
                    frozenset({ClusterRole.WORKER}),
                    NodeId("worker"),
                    has_active_job=True,
                ),
            ),
            epoch=CoordinatorEpoch(2, NodeId("coord"), "token", 1.0, 5.0),
        )
        restored = role_state_from_dict(role_state_to_dict(state))
        assignment = restored.assignment_for(NodeId("worker"))
        if assignment is None:
            self.fail("persisted role assignment was lost")
        self.assertFalse(assignment.has_active_job)
        self.assertEqual(restored.epoch, state.epoch)

    def test_role_state_rejects_duplicate_active_coordinators(self) -> None:
        with self.assertRaises(ValueError):
            role_state_from_dict(
                {
                    "assignments": [
                        {"node_id": "a", "roles": ["coordinator"]},
                        {"node_id": "b", "roles": ["coordinator"]},
                    ]
                }
            )

    def test_role_state_ignores_unbound_assignments_for_role_invariants(self) -> None:
        state = RoleState(
            assignments=(
                RoleAssignment(frozenset({ClusterRole.WORKER})),
                self.coordinator,
            )
        )

        self.assertIsNone(state.assignment_for(NodeId("unbound")))
        self.assertEqual(state.active_coordinator(), self.coordinator)

    def test_unbound_exclusive_roles_cannot_override_bound_roles(self) -> None:
        state = RoleState(
            assignments=(
                RoleAssignment(frozenset({ClusterRole.COORDINATOR})),
                self.coordinator,
                RoleAssignment(frozenset({ClusterRole.SUBCOORDINATOR})),
                self.sub,
            ),
            epoch=CoordinatorEpoch(1, NodeId("coord"), "token", 0.0, 10.0),
        )

        decision = promote_subcoordinator(state, now=10.0)

        self.assertEqual(state.active_coordinator(), self.coordinator)
        self.assertEqual(decision.epoch.coordinator_id, NodeId("sub"))

    def test_unbound_assignments_are_not_written_to_strict_state_codec(self) -> None:
        encoded = encode_role_state(
            RoleState(
                assignments=(
                    RoleAssignment(frozenset({ClusterRole.WORKER})),
                    self.worker,
                )
            )
        )

        self.assertEqual(
            encoded["assignments"],
            [
                {
                    "node_id": "worker",
                    "roles": ["worker"],
                    "paused": False,
                    "revoked": False,
                    "has_active_job": False,
                }
            ],
        )

    def test_coordinator_can_grant_scoped_subcoordinator_permission(self) -> None:
        state = RoleState(assignments=(self.coordinator, self.sub, self.worker))
        updated = state.grant_capabilities(
            actor=self.coordinator,
            subject=NodeId("sub"),
            target=NodeId("worker"),
            permissions=frozenset({NodePermission.READ_STATE}),
            now=10.0,
            expires_at=20.0,
        )
        self.assertTrue(
            updated.allows(
                subject=NodeId("sub"),
                target=NodeId("worker"),
                permission=NodePermission.READ_STATE,
                now=15.0,
            )
        )

    def test_scoped_grant_expires_and_revocation_is_immediate(self) -> None:
        state = RoleState(assignments=(self.coordinator, self.sub, self.worker))
        state = state.grant_capabilities(
            actor=self.coordinator,
            subject=NodeId("sub"),
            target=NodeId("worker"),
            permissions=frozenset({NodePermission.READ_STATE}),
            now=10.0,
            expires_at=20.0,
        )
        self.assertFalse(
            state.allows(
                subject=NodeId("sub"),
                target=NodeId("worker"),
                permission=NodePermission.READ_STATE,
                now=20.0,
            )
        )
        revoked = state.revoke_capabilities(
            actor=self.coordinator,
            subject=NodeId("sub"),
            target=NodeId("worker"),
        )
        self.assertIsNone(revoked.capability_grant(NodeId("sub"), NodeId("worker")))

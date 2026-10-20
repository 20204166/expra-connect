import unittest

from expra_connect.cluster import (
    ClusterRole,
    CoordinatorEpoch,
    RoleAssignment,
    RoleState,
)
from expra_connect.cluster.failover import FailoverCoordinator
from expra_connect.identity import NodeId


class FailoverTests(unittest.TestCase):
    def _state(self) -> RoleState:
        return RoleState(
            assignments=(
                RoleAssignment(frozenset({ClusterRole.COORDINATOR}), NodeId("coord")),
                RoleAssignment(frozenset({ClusterRole.SUBCOORDINATOR}), NodeId("sub")),
            ),
            epoch=CoordinatorEpoch(7, NodeId("coord"), "token", 0.0, 100.0),
        )

    def test_heartbeat_persists_only_current_fenced_epoch(self) -> None:
        saved: list[RoleState] = []
        manager = FailoverCoordinator(self._state(), persist=saved.append)
        self.assertFalse(
            manager.heartbeat(
                coordinator_id=NodeId("coord"), fencing_token="old", now=10.0
            )
        )
        self.assertTrue(
            manager.heartbeat(
                coordinator_id=NodeId("coord"), fencing_token="token", now=10.0
            )
        )
        self.assertEqual(len(saved), 1)
        if saved[0].epoch is None:
            self.fail("heartbeat removed the coordinator epoch")
        self.assertEqual(saved[0].epoch.issued_at, 10.0)

    def test_expired_coordinator_promotes_once_and_persists(self) -> None:
        saved: list[RoleState] = []
        manager = FailoverCoordinator(self._state(), persist=saved.append)
        self.assertTrue(manager.promote_if_due(now=100.1))
        self.assertFalse(manager.promote_if_due(now=100.2))
        if manager.state.epoch is None:
            self.fail("promotion removed the coordinator epoch")
        self.assertEqual(manager.state.epoch.coordinator_id, NodeId("sub"))
        self.assertEqual(len(saved), 1)

    def test_stale_rejoin_is_rejected_without_mutation(self) -> None:
        manager = FailoverCoordinator(self._state())
        with self.assertRaises(ValueError):
            manager.rejoin_as_worker(node_id=NodeId("coord"), current_epoch=6)
        assignment = manager.state.assignment_for(NodeId("coord"))
        if assignment is None:
            self.fail("stale rejoin removed the assignment")
        self.assertIn(ClusterRole.COORDINATOR, assignment.roles)

    def test_role_assignment_is_persisted_before_becoming_current(self) -> None:
        saved: list[RoleState] = []
        initial = self._state()
        actor = initial.assignment_for(NodeId("coord"))
        assert actor is not None
        manager = FailoverCoordinator(initial, persist=saved.append)

        change = manager.assign_role(
            actor=actor,
            target=NodeId("worker"),
            roles=frozenset({ClusterRole.WORKER}),
            now=25.0,
        )

        self.assertEqual(change.node_id, NodeId("worker"))
        self.assertEqual(
            manager.state.assignment_for(NodeId("worker")), change.assignment
        )
        self.assertEqual(saved, [manager.state])

    def test_role_assignment_failure_does_not_publish_unpersisted_state(self) -> None:
        initial = self._state()

        def fail_persist(_state: RoleState) -> None:
            raise OSError("simulated persistence failure")

        manager = FailoverCoordinator(initial, persist=fail_persist)
        actor = initial.assignment_for(NodeId("coord"))
        assert actor is not None

        with self.assertRaises(OSError):
            manager.assign_role(
                actor=actor,
                target=NodeId("worker"),
                roles=frozenset({ClusterRole.WORKER}),
                now=25.0,
            )

        self.assertIs(manager.state, initial)
        self.assertIsNone(manager.state.assignment_for(NodeId("worker")))

    def test_member_revocation_is_persisted(self) -> None:
        saved: list[RoleState] = []
        base = self._state()
        actor = base.assignment_for(NodeId("coord"))
        assert actor is not None
        initial = base.assign(
            actor=actor,
            target=NodeId("worker"),
            roles=frozenset({ClusterRole.WORKER}),
            now=10.0,
        )[0]
        manager = FailoverCoordinator(initial, persist=saved.append)

        manager.revoke_member(actor=actor, target=NodeId("worker"))

        assignment = manager.state.assignment_for(NodeId("worker"))
        self.assertIsNotNone(assignment)
        assert assignment is not None
        self.assertTrue(assignment.revoked)
        self.assertEqual(saved, [manager.state])

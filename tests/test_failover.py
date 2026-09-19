import unittest

from expra_connect.failover import FailoverCoordinator
from expra_connect.identity import NodeId
from expra_connect.role_engine import (
    ClusterRole,
    CoordinatorEpoch,
    RoleAssignment,
    RoleState,
)


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

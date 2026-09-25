"""Test-first hardening for the pure cluster role / capability / fencing layer.

These regressions encode the canonical invariants that Expra Connect must hold
even where the mature ``exp`` reference implementation was weaker. Every test
was written before the corresponding production change; the production diff is
limited to the gaps these tests actually demonstrate.
"""

from __future__ import annotations

import threading
import unittest
from dataclasses import replace

from expra_connect.cluster import (
    CapabilityGrant,
    Cluster,
    ClusterRole,
    ClusterState,
    CoordinatorEpoch,
    FailoverCoordinator,
    FencingError,
    RoleAssignment,
    RoleAuthorizationError,
    RoleChange,
    RoleState,
    can_promote,
    promote_subcoordinator,
    rejoin_as_worker,
    renew_lease,
    role_state_from_dict,
    role_state_to_dict,
)
from expra_connect.identity import NodeId
from expra_connect.models import NodePermission

PERMISSION = NodePermission.READ_STATE


def coordinator(node: str = "coord") -> RoleAssignment:
    return RoleAssignment(frozenset({ClusterRole.COORDINATOR}), NodeId(node))


def subcoordinator(node: str = "sub", **kwargs: object) -> RoleAssignment:
    return RoleAssignment(
        frozenset({ClusterRole.SUBCOORDINATOR}),
        NodeId(node),
        **kwargs,  # type: ignore[arg-type]
    )


def worker(node: str = "worker", **kwargs: object) -> RoleAssignment:
    return RoleAssignment(
        frozenset({ClusterRole.WORKER}),
        NodeId(node),
        **kwargs,  # type: ignore[arg-type]
    )


class RoleAuthorityTests(unittest.TestCase):
    """Role authority must come from canonical RoleState, not the caller object."""

    def setUp(self) -> None:
        self.coord = coordinator()
        self.worker = worker()
        self.sub = subcoordinator()

    def test_forged_coordinator_assignment_cannot_control(self) -> None:
        state = RoleState(assignments=(self.coord, self.worker))
        forged = coordinator("attacker")
        with self.assertRaises(RoleAuthorizationError):
            state.assign(
                actor=forged,
                target=NodeId("worker"),
                roles=frozenset({ClusterRole.SUBCOORDINATOR}),
            )

    def test_forged_coordinator_assignment_cannot_pause_or_revoke(self) -> None:
        state = RoleState(assignments=(self.coord, self.worker))
        forged = coordinator("attacker")
        with self.assertRaises(RoleAuthorizationError):
            state.pause(actor=forged, target=NodeId("worker"))
        with self.assertRaises(RoleAuthorizationError):
            state.revoke(actor=forged, target=NodeId("worker"))

    def test_forged_coordinator_assignment_cannot_grant_capabilities(self) -> None:
        state = RoleState(assignments=(self.coord, self.sub, self.worker))
        forged = coordinator("attacker")
        with self.assertRaises(RoleAuthorizationError):
            state.grant_capabilities(
                actor=forged,
                subject=NodeId("sub"),
                target=NodeId("worker"),
                permissions=frozenset({PERMISSION}),
                now=1.0,
                expires_at=100.0,
            )

    def test_unbound_coordinator_actor_cannot_control(self) -> None:
        state = RoleState(assignments=(self.coord, self.worker))
        unbound = RoleAssignment(frozenset({ClusterRole.COORDINATOR}))
        with self.assertRaises(RoleAuthorizationError):
            state.pause(actor=unbound, target=NodeId("worker"))

    def test_unbound_actor_cannot_assign_or_revoke(self) -> None:
        state = RoleState(assignments=(self.coord, self.worker))
        unbound = RoleAssignment(frozenset({ClusterRole.COORDINATOR}))
        with self.assertRaises(RoleAuthorizationError):
            state.assign(
                actor=unbound,
                target=NodeId("new"),
                roles=frozenset({ClusterRole.WORKER}),
            )
        with self.assertRaises(RoleAuthorizationError):
            state.revoke(actor=unbound, target=NodeId("worker"))

    def test_stale_former_coordinator_cannot_control(self) -> None:
        # A stale former Coordinator keeps its Coordinator role but no longer
        # owns the active epoch.  Canonical authority must not be granted.
        stale = coordinator("old")
        state = RoleState(
            assignments=(stale, self.worker),
            epoch=CoordinatorEpoch(2, NodeId("new"), "fence", 0.0, 100.0),
        )
        with self.assertRaises(RoleAuthorizationError):
            state.pause(actor=stale, target=NodeId("worker"))

    def test_paused_canonical_coordinator_cannot_control(self) -> None:
        paused = RoleAssignment(
            frozenset({ClusterRole.COORDINATOR}), NodeId("coord"), paused=True
        )
        state = RoleState(assignments=(paused, self.worker))
        with self.assertRaises(RoleAuthorizationError):
            state.pause(actor=paused, target=NodeId("worker"))

    def test_revoked_canonical_coordinator_cannot_control(self) -> None:
        revoked = RoleAssignment(
            frozenset({ClusterRole.COORDINATOR}), NodeId("coord"), revoked=True
        )
        state = RoleState(assignments=(revoked, self.worker))
        with self.assertRaises(RoleAuthorizationError):
            state.revoke(actor=revoked, target=NodeId("worker"))

    def test_worker_and_subcoordinator_cannot_control(self) -> None:
        state = RoleState(assignments=(self.coord, self.worker, self.sub))
        for actor in (self.worker, self.sub):
            with self.assertRaises(RoleAuthorizationError):
                state.pause(actor=actor, target=NodeId("worker"))

    def test_role_change_actor_id_is_the_canonical_actor(self) -> None:
        state = RoleState(assignments=(self.coord,))
        _updated, change = state.assign(
            actor=self.coord,
            target=NodeId("new"),
            roles=frozenset({ClusterRole.WORKER}),
        )
        self.assertEqual(change.actor_id, NodeId("coord"))

    def test_denied_transition_does_not_mutate_state(self) -> None:
        state = RoleState(assignments=(self.coord, self.worker))
        forged = coordinator("attacker")
        with self.assertRaises(RoleAuthorizationError):
            state.pause(actor=forged, target=NodeId("worker"))
        self.assertEqual(state.assignment_for(NodeId("worker")), self.worker)


class CoordinatorSelfControlTests(unittest.TestCase):
    """Coordinator ownership changes only through failover/transfer semantics."""

    def setUp(self) -> None:
        self.coord = coordinator()

    def test_active_coordinator_cannot_assign_itself(self) -> None:
        state = RoleState(assignments=(self.coord,))
        with self.assertRaises(RoleAuthorizationError):
            state.assign(
                actor=self.coord,
                target=NodeId("coord"),
                roles=frozenset({ClusterRole.WORKER}),
            )

    def test_active_coordinator_cannot_pause_or_revoke_itself(self) -> None:
        state = RoleState(assignments=(self.coord,))
        with self.assertRaises(RoleAuthorizationError):
            state.pause(actor=self.coord, target=NodeId("coord"))
        with self.assertRaises(RoleAuthorizationError):
            state.revoke(actor=self.coord, target=NodeId("coord"))

    def test_coordinator_can_manage_its_own_worker_job(self) -> None:
        # A Coordinator is also a Worker; job state is not authority ownership.
        state = RoleState(assignments=(self.coord,))
        assigned = state.assign_job(actor=self.coord, target=NodeId("coord"))
        assignment = assigned.assignment_for(NodeId("coord"))
        assert assignment is not None
        self.assertTrue(assignment.has_active_job)


class RoleTransitionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.coord = coordinator()
        self.worker = worker()
        self.sub = subcoordinator()

    def test_assign_preserves_pause_state(self) -> None:
        paused = worker(paused=True)
        state = RoleState(assignments=(self.coord, paused))
        updated, _ = state.assign(
            actor=self.coord,
            target=NodeId("worker"),
            roles=frozenset({ClusterRole.WORKER}),
        )
        assignment = updated.assignment_for(NodeId("worker"))
        assert assignment is not None
        self.assertTrue(assignment.paused)

    def test_assign_preserves_job_for_worker_role(self) -> None:
        active = worker(has_active_job=True)
        state = RoleState(assignments=(self.coord, active))
        updated, _ = state.assign(
            actor=self.coord,
            target=NodeId("worker"),
            roles=frozenset({ClusterRole.WORKER, ClusterRole.SUBCOORDINATOR}),
        )
        assignment = updated.assignment_for(NodeId("worker"))
        assert assignment is not None
        self.assertTrue(assignment.has_active_job)

    def test_assign_clears_job_when_worker_role_is_removed(self) -> None:
        active = worker(has_active_job=True)
        state = RoleState(assignments=(self.coord, active))
        updated, _ = state.assign(
            actor=self.coord,
            target=NodeId("worker"),
            roles=frozenset({ClusterRole.SUBCOORDINATOR}),
        )
        assignment = updated.assignment_for(NodeId("worker"))
        assert assignment is not None
        self.assertFalse(assignment.has_active_job)

    def test_revoked_node_cannot_be_reassigned(self) -> None:
        revoked = worker(revoked=True)
        state = RoleState(assignments=(self.coord, revoked))
        with self.assertRaises(RoleAuthorizationError):
            state.assign(
                actor=self.coord,
                target=NodeId("worker"),
                roles=frozenset({ClusterRole.WORKER}),
            )

    def test_clear_revocation_is_pure_and_never_implicit(self) -> None:
        revoked = worker(revoked=True)
        state = RoleState(assignments=(self.coord, revoked))
        cleared = state.clear_revocation(NodeId("worker"))
        self.assertIsNone(cleared.assignment_for(NodeId("worker")))
        # clear_revocation never runs implicitly; assign still rejects the node.
        with self.assertRaises(RoleAuthorizationError):
            state.assign(
                actor=self.coord,
                target=NodeId("worker"),
                roles=frozenset({ClusterRole.WORKER}),
            )
        active = RoleState(assignments=(self.coord, worker()))
        self.assertIs(active.clear_revocation(NodeId("worker")), active)

    def test_role_assignment_rejects_coordinator_subcoordinator_combo(self) -> None:
        with self.assertRaises(ValueError):
            RoleAssignment(
                frozenset({ClusterRole.COORDINATOR, ClusterRole.SUBCOORDINATOR}),
                NodeId("x"),
            )

    def test_role_state_rejects_duplicate_active_coordinators(self) -> None:
        with self.assertRaises(ValueError):
            RoleState(assignments=(self.coord, coordinator("other")))

    def test_role_state_rejects_duplicate_active_subcoordinators(self) -> None:
        with self.assertRaises(ValueError):
            RoleState(assignments=(self.sub, subcoordinator("other")))


class RevokePausedMemberTests(unittest.TestCase):
    def test_revoke_paused_worker_succeeds_and_clears_pause_and_job(self) -> None:
        paused = worker(paused=True, has_active_job=True)
        state = RoleState(assignments=(coordinator(), paused))
        updated = state.revoke(actor=coordinator(), target=NodeId("worker"))
        assignment = updated.assignment_for(NodeId("worker"))
        assert assignment is not None
        self.assertTrue(assignment.revoked)
        self.assertFalse(assignment.paused)
        self.assertFalse(assignment.has_active_job)

    def test_cluster_facade_revoke_paused_worker_succeeds(self) -> None:
        cluster = Cluster(NodeId("coord"), clock=lambda: 10.0)
        cluster.assign(NodeId("worker"), ClusterRole.WORKER)
        cluster.assignments[NodeId("worker")] = replace(
            cluster.assignments[NodeId("worker")], paused=True
        )
        cluster.revoke(NodeId("worker"))
        self.assertFalse(cluster.is_member(NodeId("worker")))


class JobOperationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.coord = coordinator()
        self.worker = worker()

    def test_assign_job_requires_active_worker(self) -> None:
        state = RoleState(assignments=(self.coord, self.worker))
        updated = state.assign_job(actor=self.coord, target=NodeId("worker"))
        assignment = updated.assignment_for(NodeId("worker"))
        assert assignment is not None
        self.assertTrue(assignment.has_active_job)

    def test_assign_job_to_paused_worker_is_denied(self) -> None:
        paused = worker(paused=True)
        state = RoleState(assignments=(self.coord, paused))
        with self.assertRaises(RoleAuthorizationError):
            state.assign_job(actor=self.coord, target=NodeId("worker"))

    def test_remove_job_from_paused_worker_is_allowed(self) -> None:
        paused = worker(paused=True, has_active_job=True)
        state = RoleState(assignments=(self.coord, paused))
        updated = state.remove_job(actor=self.coord, target=NodeId("worker"))
        assignment = updated.assignment_for(NodeId("worker"))
        assert assignment is not None
        self.assertFalse(assignment.has_active_job)
        self.assertTrue(assignment.paused)

    def test_job_operations_reject_revoked_unknown_and_non_worker(self) -> None:
        revoked = worker(revoked=True)
        sub = subcoordinator()
        state = RoleState(assignments=(self.coord, revoked, sub))
        for target in (NodeId("worker"), NodeId("ghost"), NodeId("sub")):
            with self.assertRaises(RoleAuthorizationError):
                state.assign_job(actor=self.coord, target=target)
            with self.assertRaises(RoleAuthorizationError):
                state.remove_job(actor=self.coord, target=target)


class CapabilityGrantTests(unittest.TestCase):
    def setUp(self) -> None:
        self.coord = coordinator()
        self.sub = subcoordinator()
        self.worker = worker()

    def _granted(self) -> RoleState:
        state = RoleState(assignments=(self.coord, self.sub, self.worker))
        return state.grant_capabilities(
            actor=self.coord,
            subject=NodeId("sub"),
            target=NodeId("worker"),
            permissions=frozenset({PERMISSION}),
            now=1.0,
            expires_at=100.0,
        )

    def test_canonical_coordinator_can_grant_and_forged_cannot(self) -> None:
        state = self._granted()
        self.assertIsNotNone(state.capability_grant(NodeId("sub"), NodeId("worker")))
        with self.assertRaises(RoleAuthorizationError):
            state.grant_capabilities(
                actor=coordinator("attacker"),
                subject=NodeId("sub"),
                target=NodeId("worker"),
                permissions=frozenset({PERMISSION}),
                now=1.0,
                expires_at=100.0,
            )

    def test_paused_subcoordinator_cannot_receive_new_grant(self) -> None:
        state = RoleState(
            assignments=(self.coord, subcoordinator(paused=True), self.worker)
        )
        with self.assertRaises(RoleAuthorizationError):
            state.grant_capabilities(
                actor=self.coord,
                subject=NodeId("sub"),
                target=NodeId("worker"),
                permissions=frozenset({PERMISSION}),
                now=1.0,
                expires_at=100.0,
            )

    def test_paused_worker_cannot_be_a_new_grant_target(self) -> None:
        state = RoleState(assignments=(self.coord, self.sub, worker(paused=True)))
        with self.assertRaises(RoleAuthorizationError):
            state.grant_capabilities(
                actor=self.coord,
                subject=NodeId("sub"),
                target=NodeId("worker"),
                permissions=frozenset({PERMISSION}),
                now=1.0,
                expires_at=100.0,
            )

    def test_expired_grant_exact_boundary_fails_closed(self) -> None:
        state = self._granted()
        self.assertIsNotNone(
            state.capability_grant(NodeId("sub"), NodeId("worker"), now=99.999)
        )
        self.assertIsNone(
            state.capability_grant(NodeId("sub"), NodeId("worker"), now=100.0)
        )
        self.assertFalse(
            state.allows(
                subject=NodeId("sub"),
                target=NodeId("worker"),
                permission=PERMISSION,
                now=100.0,
            )
        )

    def test_invalid_now_makes_capability_checks_fail_closed(self) -> None:
        state = self._granted()
        for bad in (float("nan"), float("-inf"), True, "1"):
            self.assertIsNone(
                state.capability_grant(
                    NodeId("sub"),
                    NodeId("worker"),
                    now=bad,  # type: ignore[arg-type]
                )
            )
            self.assertFalse(
                state.allows(
                    subject=NodeId("sub"),
                    target=NodeId("worker"),
                    permission=PERMISSION,
                    now=bad,  # type: ignore[arg-type]
                )
            )

    def test_demoting_subcoordinator_drops_its_grants(self) -> None:
        state = self._granted()
        demoted, _ = state.assign(
            actor=self.coord,
            target=NodeId("sub"),
            roles=frozenset({ClusterRole.WORKER}),
        )
        self.assertIsNone(demoted.capability_grant(NodeId("sub"), NodeId("worker")))

    def test_stale_grant_cannot_resurrect_after_role_restored(self) -> None:
        state = self._granted()
        demoted, _ = state.assign(
            actor=self.coord,
            target=NodeId("sub"),
            roles=frozenset({ClusterRole.WORKER}),
        )
        restored, _ = demoted.assign(
            actor=self.coord,
            target=NodeId("sub"),
            roles=frozenset({ClusterRole.SUBCOORDINATOR}),
        )
        self.assertFalse(
            restored.allows(
                subject=NodeId("sub"),
                target=NodeId("worker"),
                permission=PERMISSION,
                now=2.0,
            )
        )

    def test_allows_fails_closed_for_missing_target_assignment(self) -> None:
        # A malformed/stale state may retain a grant whose target is gone.
        state = self._granted()
        orphaned = replace(state, assignments=(self.coord, self.sub))
        self.assertFalse(
            orphaned.allows(
                subject=NodeId("sub"),
                target=NodeId("worker"),
                permission=PERMISSION,
                now=2.0,
            )
        )

    def test_coordinator_cannot_reach_non_worker_subcoordinator(self) -> None:
        other_sub = subcoordinator("sub2")
        state = RoleState(assignments=(self.coord, other_sub))
        self.assertFalse(
            state.allows(
                subject=NodeId("coord"),
                target=NodeId("sub2"),
                permission=PERMISSION,
                now=2.0,
            )
        )

    def test_allows_rejects_paused_source_and_target(self) -> None:
        paused_worker = worker(paused=True)
        state = RoleState(assignments=(self.coord, self.sub, paused_worker))
        self.assertFalse(
            state.allows(
                subject=NodeId("sub"),
                target=NodeId("worker"),
                permission=PERMISSION,
                now=2.0,
            )
        )
        self.assertFalse(
            state.allows(
                subject=NodeId("coord"),
                target=NodeId("worker"),
                permission=PERMISSION,
                now=2.0,
            )
        )

    def test_coordinator_full_authority_and_unknown_target(self) -> None:
        state = RoleState(assignments=(self.coord, self.worker))
        self.assertTrue(
            state.allows(
                subject=NodeId("coord"),
                target=NodeId("worker"),
                permission=NodePermission.CLEANUP,
                now=2.0,
            )
        )
        self.assertFalse(
            state.allows(
                subject=NodeId("coord"),
                target=NodeId("ghost"),
                permission=PERMISSION,
                now=2.0,
            )
        )

    def test_self_access_matrix(self) -> None:
        state = RoleState(
            assignments=(
                self.coord,
                self.sub,
                self.worker,
                worker("paused", paused=True),
                worker("revoked", revoked=True),
            )
        )
        for node in ("coord", "sub", "worker"):
            self.assertTrue(
                state.allows(
                    subject=NodeId(node),
                    target=NodeId(node),
                    permission=NodePermission.CLEANUP,
                    now=2.0,
                )
            )
        self.assertFalse(
            state.allows(
                subject=NodeId("paused"),
                target=NodeId("paused"),
                permission=PERMISSION,
                now=2.0,
            )
        )
        self.assertFalse(
            state.allows(
                subject=NodeId("revoked"),
                target=NodeId("revoked"),
                permission=PERMISSION,
                now=2.0,
            )
        )

    def test_revoke_member_removes_related_grants_only(self) -> None:
        worker_a = worker("worker-a")
        worker_b = worker("worker-b")
        state = RoleState(assignments=(self.coord, self.sub, worker_a, worker_b))
        state = state.grant_capabilities(
            actor=self.coord,
            subject=NodeId("sub"),
            target=NodeId("worker-a"),
            permissions=frozenset({PERMISSION}),
            now=1.0,
            expires_at=100.0,
        )
        # Revoking an unrelated worker leaves the grant intact.
        unrelated = state.revoke(actor=self.coord, target=NodeId("worker-b"))
        self.assertIsNotNone(
            unrelated.capability_grant(NodeId("sub"), NodeId("worker-a"))
        )
        # Revoking the grant target removes the grant.
        revoked = state.revoke(actor=self.coord, target=NodeId("worker-a"))
        self.assertIsNone(revoked.capability_grant(NodeId("sub"), NodeId("worker-a")))

    def test_revoke_subject_removes_all_its_grants(self) -> None:
        state = self._granted()
        revoked = state.revoke(actor=self.coord, target=NodeId("sub"))
        self.assertEqual(revoked.capability_grants, ())

    def test_revoke_capabilities_is_idempotent(self) -> None:
        state = self._granted()
        revoked = state.revoke_capabilities(
            actor=self.coord, subject=NodeId("sub"), target=NodeId("worker")
        )
        again = revoked.revoke_capabilities(
            actor=self.coord, subject=NodeId("sub"), target=NodeId("worker")
        )
        self.assertIsNone(again.capability_grant(NodeId("sub"), NodeId("worker")))
        self.assertEqual(again.capability_grants, ())

    def test_grant_replacement_does_not_duplicate(self) -> None:
        state = self._granted()
        replaced = state.grant_capabilities(
            actor=self.coord,
            subject=NodeId("sub"),
            target=NodeId("worker"),
            permissions=frozenset({NodePermission.CLEANUP}),
            now=5.0,
            expires_at=50.0,
        )
        self.assertEqual(len(replaced.capability_grants), 1)
        grant = replaced.capability_grant(NodeId("sub"), NodeId("worker"))
        assert grant is not None
        self.assertEqual(grant.permissions, frozenset({NodePermission.CLEANUP}))
        self.assertEqual(grant.issued_at, 5.0)


class LeaseTests(unittest.TestCase):
    def setUp(self) -> None:
        self.epoch = CoordinatorEpoch(1, NodeId("coord"), "token", 0.0, 100.0)

    def test_renewal_before_expiry_is_allowed(self) -> None:
        renewed = renew_lease(
            self.epoch,
            coordinator_id=NodeId("coord"),
            fencing_token="token",
            now=99.0,
        )
        self.assertGreaterEqual(renewed.lease_expires_at, self.epoch.lease_expires_at)

    def test_exact_expiry_rejects_renewal_but_allows_promotion(self) -> None:
        state = RoleState(
            assignments=(coordinator(), subcoordinator()), epoch=self.epoch
        )
        self.assertTrue(can_promote(state, subcoordinator_id=NodeId("sub"), now=100.0))
        with self.assertRaises(FencingError):
            renew_lease(
                self.epoch,
                coordinator_id=NodeId("coord"),
                fencing_token="token",
                now=100.0,
            )

    def test_expired_lease_cannot_be_resurrected(self) -> None:
        for now in (100.001, 1000.0):
            with self.assertRaises(FencingError):
                renew_lease(
                    self.epoch,
                    coordinator_id=NodeId("coord"),
                    fencing_token="token",
                    now=now,
                )

    def test_wrong_coordinator_or_fence_is_rejected(self) -> None:
        with self.assertRaises(FencingError):
            renew_lease(
                self.epoch,
                coordinator_id=NodeId("other"),
                fencing_token="token",
                now=1.0,
            )
        with self.assertRaises(FencingError):
            renew_lease(
                self.epoch,
                coordinator_id=NodeId("coord"),
                fencing_token="wrong",
                now=1.0,
            )
        with self.assertRaises(FencingError):
            renew_lease(
                self.epoch,
                coordinator_id=NodeId("coord"),
                fencing_token=None,  # type: ignore[arg-type]
                now=1.0,
            )

    def test_invalid_now_is_rejected(self) -> None:
        for bad in (float("nan"), float("inf"), float("-inf"), True, "1"):
            with self.assertRaises((FencingError, ValueError, TypeError)):
                renew_lease(
                    self.epoch,
                    coordinator_id=NodeId("coord"),
                    fencing_token="token",
                    now=bad,  # type: ignore[arg-type]
                )

    def test_invalid_lease_duration_is_rejected(self) -> None:
        for bad in (float("nan"), float("inf"), float("-inf"), 0.0, -1.0, True):
            with self.assertRaises((FencingError, ValueError, TypeError)):
                renew_lease(
                    self.epoch,
                    coordinator_id=NodeId("coord"),
                    fencing_token="token",
                    now=1.0,
                    lease_seconds=bad,
                )

    def test_backdated_renewal_does_not_regress_or_shorten(self) -> None:
        epoch = CoordinatorEpoch(1, NodeId("coord"), "token", 50.0, 200.0)
        renewed = renew_lease(
            epoch,
            coordinator_id=NodeId("coord"),
            fencing_token="token",
            now=10.0,
        )
        self.assertGreaterEqual(renewed.issued_at, epoch.issued_at)
        self.assertGreaterEqual(renewed.lease_expires_at, epoch.lease_expires_at)


class PromotionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.coord = coordinator()
        self.sub = subcoordinator()
        self.worker = worker()
        self.epoch = CoordinatorEpoch(7, NodeId("coord"), "token", 0.0, 100.0)

    def test_promotion_before_expiry_is_denied(self) -> None:
        state = RoleState(assignments=(self.coord, self.sub), epoch=self.epoch)
        with self.assertRaises(FencingError):
            promote_subcoordinator(state, now=99.0)

    def test_promotion_after_expiry_is_allowed(self) -> None:
        state = RoleState(assignments=(self.coord, self.sub), epoch=self.epoch)
        decision = promote_subcoordinator(state, now=100.1)
        self.assertEqual(decision.epoch.epoch, 8)
        self.assertEqual(decision.epoch.coordinator_id, NodeId("sub"))

    def test_promotion_requires_explicit_boolean_authentication(self) -> None:
        state = RoleState(assignments=(self.coord, self.sub), epoch=self.epoch)
        for bad in ("false", "0", 1, [], object()):
            self.assertFalse(
                can_promote(
                    state,
                    subcoordinator_id=NodeId("sub"),
                    now=100.0,
                    authenticated=bad,  # type: ignore[arg-type]
                )
            )
            with self.assertRaises(FencingError):
                promote_subcoordinator(
                    state,
                    now=100.0,
                    authenticated=bad,  # type: ignore[arg-type]
                )
        self.assertTrue(
            can_promote(
                state, subcoordinator_id=NodeId("sub"), now=100.0, authenticated=True
            )
        )

    def test_paused_revoked_worker_and_missing_sub_cannot_promote(self) -> None:
        for assignment in (
            subcoordinator(paused=True),
            subcoordinator(revoked=True),
            worker(),
        ):
            state = RoleState(assignments=(self.coord, assignment), epoch=self.epoch)
            with self.assertRaises(FencingError):
                promote_subcoordinator(state, now=100.0)
        with self.assertRaises(FencingError):
            promote_subcoordinator(
                RoleState(assignments=(self.coord,), epoch=self.epoch), now=100.0
            )
        with self.assertRaises(FencingError):
            promote_subcoordinator(
                RoleState(assignments=(self.coord, self.sub), epoch=None), now=100.0
            )

    def test_consumed_promotion_epoch_cannot_replay(self) -> None:
        state = RoleState(
            assignments=(self.coord, self.sub),
            epoch=self.epoch,
            promotion_epochs=frozenset({7}),
        )
        with self.assertRaises(FencingError):
            promote_subcoordinator(state, now=100.0)

    def test_unbound_subcoordinator_cannot_promote(self) -> None:
        state = RoleState(
            assignments=(
                self.coord,
                RoleAssignment(frozenset({ClusterRole.SUBCOORDINATOR})),
            ),
            epoch=self.epoch,
        )
        with self.assertRaises(FencingError):
            promote_subcoordinator(state, now=100.0)

    def test_promotion_produces_exactly_one_coordinator_and_demotes_others(
        self,
    ) -> None:
        other_worker = worker("worker2")
        state = RoleState(
            assignments=(self.coord, self.sub, other_worker), epoch=self.epoch
        )
        decision = promote_subcoordinator(state, now=100.0)
        active_coordinators = [
            a
            for a in decision.assignments
            if not a.revoked and ClusterRole.COORDINATOR in a.roles
        ]
        self.assertEqual(len(active_coordinators), 1)
        self.assertEqual(active_coordinators[0].node_id, NodeId("sub"))
        promoted = next(a for a in decision.assignments if a.node_id == NodeId("sub"))
        self.assertEqual(
            promoted.roles, frozenset({ClusterRole.COORDINATOR, ClusterRole.WORKER})
        )
        old = next(a for a in decision.assignments if a.node_id == NodeId("coord"))
        self.assertEqual(old.roles, frozenset({ClusterRole.WORKER}))

    def test_promotion_prunes_grants_whose_subject_promotes(self) -> None:
        state = RoleState(
            assignments=(self.coord, self.sub, self.worker), epoch=self.epoch
        )
        state = state.grant_capabilities(
            actor=self.coord,
            subject=NodeId("sub"),
            target=NodeId("worker"),
            permissions=frozenset({PERMISSION}),
            now=1.0,
            expires_at=100.0,
        )
        decision = promote_subcoordinator(state, now=100.0)
        self.assertEqual(decision.capability_grants, ())

    def test_failover_prunes_ineligible_grants(self) -> None:
        state = RoleState(
            assignments=(self.coord, self.sub, self.worker), epoch=self.epoch
        )
        state = state.grant_capabilities(
            actor=self.coord,
            subject=NodeId("sub"),
            target=NodeId("worker"),
            permissions=frozenset({PERMISSION}),
            now=1.0,
            expires_at=100.0,
        )
        manager = FailoverCoordinator(state)
        self.assertTrue(manager.promote_if_due(now=100.0))
        self.assertEqual(manager.state.capability_grants, ())

    def test_promotion_preserves_demoted_coordinator_worker_job_until_rejoin(
        self,
    ) -> None:
        # The demoted Coordinator keeps its Worker role, so its job record is
        # retained here; rejoin_as_worker is the owner that clears it.
        active_coord = RoleAssignment(
            frozenset({ClusterRole.COORDINATOR, ClusterRole.WORKER}),
            NodeId("coord"),
            has_active_job=True,
        )
        state = RoleState(assignments=(active_coord, self.sub), epoch=self.epoch)
        decision = promote_subcoordinator(state, now=100.0)
        old = next(a for a in decision.assignments if a.node_id == NodeId("coord"))
        self.assertEqual(old.roles, frozenset({ClusterRole.WORKER}))
        self.assertTrue(old.has_active_job)


class RejoinTests(unittest.TestCase):
    def setUp(self) -> None:
        self.coord = coordinator()
        self.worker = worker()

    def test_stale_lower_epoch_is_rejected(self) -> None:
        state = RoleState(
            assignments=(self.coord, self.worker),
            epoch=CoordinatorEpoch(7, NodeId("new"), "token", 0.0, 100.0),
        )
        with self.assertRaises(FencingError):
            rejoin_as_worker(state, node_id=NodeId("coord"), current_epoch=6)

    def test_future_epoch_fails_closed(self) -> None:
        state = RoleState(
            assignments=(self.coord, self.worker),
            epoch=CoordinatorEpoch(7, NodeId("new"), "token", 0.0, 100.0),
        )
        with self.assertRaises(FencingError):
            rejoin_as_worker(state, node_id=NodeId("coord"), current_epoch=8)

    def test_malformed_epoch_is_rejected(self) -> None:
        state = RoleState(
            assignments=(self.coord, self.worker),
            epoch=CoordinatorEpoch(7, NodeId("new"), "token", 0.0, 100.0),
        )
        for bad in (True, -1, 7.0, "7"):
            with self.assertRaises((FencingError, ValueError, TypeError)):
                rejoin_as_worker(
                    state,
                    node_id=NodeId("coord"),
                    current_epoch=bad,  # type: ignore[arg-type]
                )

    def test_equal_epoch_demotes_former_coordinator(self) -> None:
        state = RoleState(
            assignments=(self.coord, self.worker),
            epoch=CoordinatorEpoch(7, NodeId("new"), "token", 0.0, 100.0),
        )
        updated = rejoin_as_worker(state, node_id=NodeId("coord"), current_epoch=7)
        assignment = updated.assignment_for(NodeId("coord"))
        assert assignment is not None
        self.assertEqual(assignment.roles, frozenset({ClusterRole.WORKER}))
        self.assertFalse(assignment.paused)
        self.assertFalse(assignment.has_active_job)

    def test_non_coordinator_rejoin_is_idempotent(self) -> None:
        state = RoleState(
            assignments=(self.coord, self.worker),
            epoch=CoordinatorEpoch(7, NodeId("new"), "token", 0.0, 100.0),
        )
        self.assertIs(
            rejoin_as_worker(state, node_id=NodeId("worker"), current_epoch=7), state
        )
        self.assertIs(
            rejoin_as_worker(state, node_id=NodeId("ghost"), current_epoch=7), state
        )


class OrchestrationTests(unittest.TestCase):
    def _state(self) -> RoleState:
        return RoleState(
            assignments=(coordinator(), subcoordinator()),
            epoch=CoordinatorEpoch(7, NodeId("coord"), "token", 0.0, 100.0),
        )

    def test_promotion_persistence_failure_publishes_nothing(self) -> None:
        state = RoleState(
            assignments=(coordinator(), subcoordinator(), worker()),
            epoch=CoordinatorEpoch(7, NodeId("coord"), "token", 0.0, 100.0),
        )
        state = state.grant_capabilities(
            actor=coordinator(),
            subject=NodeId("sub"),
            target=NodeId("worker"),
            permissions=frozenset({PERMISSION}),
            now=1.0,
            expires_at=100.0,
        )
        manager = FailoverCoordinator(
            state, persist=lambda _state: (_ for _ in ()).throw(OSError("disk"))
        )
        with self.assertRaises(OSError):
            manager.promote_if_due(now=100.0)
        epoch = manager.state.epoch
        assert epoch is not None
        self.assertEqual(epoch.coordinator_id, NodeId("coord"))
        self.assertEqual(manager.state.promotion_epochs, frozenset())
        self.assertEqual(manager.state.capability_grants, state.capability_grants)

    def test_rejoin_persistence_failure_publishes_nothing(self) -> None:
        state = self._state()
        manager = FailoverCoordinator(
            state, persist=lambda _state: (_ for _ in ()).throw(OSError("disk"))
        )
        with self.assertRaises(OSError):
            manager.rejoin_as_worker(node_id=NodeId("coord"), current_epoch=7)
        assignment = manager.state.assignment_for(NodeId("coord"))
        assert assignment is not None
        self.assertIn(ClusterRole.COORDINATOR, assignment.roles)

    def test_double_promotion_commits_once(self) -> None:
        manager = FailoverCoordinator(self._state())
        barrier = threading.Barrier(2)
        results: list[bool] = []
        lock = threading.Lock()

        def attempt() -> None:
            barrier.wait()
            outcome = manager.promote_if_due(now=100.0)
            with lock:
                results.append(outcome)

        threads = [threading.Thread(target=attempt) for _ in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(results.count(True), 1)
        epoch = manager.state.epoch
        assert epoch is not None
        self.assertEqual(epoch.epoch, 8)
        self.assertEqual(manager.state.promotion_epochs, frozenset({7}))

    def test_heartbeat_and_promotion_are_mutually_exclusive_at_expiry(self) -> None:
        manager = FailoverCoordinator(self._state())
        self.assertFalse(
            manager.heartbeat(
                coordinator_id=NodeId("coord"), fencing_token="token", now=100.0
            )
        )
        self.assertTrue(manager.promote_if_due(now=100.0))

    def test_restart_preserves_consumed_promotion_epoch(self) -> None:
        state = RoleState(
            assignments=(coordinator(), subcoordinator()),
            epoch=CoordinatorEpoch(7, NodeId("coord"), "token", 0.0, 100.0),
            promotion_epochs=frozenset({6, 7}),
        )
        restored = role_state_from_dict(role_state_to_dict(state))
        self.assertEqual(restored.promotion_epochs, frozenset({6, 7}))
        with self.assertRaises(FencingError):
            promote_subcoordinator(restored, now=100.0)


class ModelHardeningTests(unittest.TestCase):
    def test_role_state_rejects_malformed_promotion_epochs(self) -> None:
        for bad in (True, -1, 1.5, "1"):
            with self.assertRaises(ValueError):
                RoleState(promotion_epochs=frozenset({bad}))  # type: ignore[arg-type]

    def test_role_change_rejects_non_finite_time(self) -> None:
        assignment = RoleAssignment(frozenset({ClusterRole.WORKER}), NodeId("worker"))
        for bad in (float("nan"), float("inf"), True, "1"):
            with self.assertRaises(ValueError):
                RoleChange(
                    NodeId("worker"),
                    assignment,
                    bad,  # type: ignore[arg-type]
                    NodeId("coord"),
                )

    def test_role_state_rejects_duplicate_capability_grant(self) -> None:
        grant = CapabilityGrant(
            NodeId("sub"), NodeId("worker"), frozenset({PERMISSION}), 1.0, 100.0
        )
        with self.assertRaises(ValueError):
            RoleState(capability_grants=(grant, grant))

    def test_fencing_token_shape(self) -> None:
        from expra_connect.cluster import new_fencing_token

        token = new_fencing_token()
        self.assertIsInstance(token, str)
        self.assertEqual(len(token), 64)
        int(token, 16)
        self.assertNotEqual(new_fencing_token(), token)


class PersistenceHardeningTests(unittest.TestCase):
    def _document(self, assignments: list[dict[str, object]]) -> dict[str, object]:
        return {
            "schema_version": 2,
            "local_node_id": "coord",
            "cluster_id": "cluster",
            "assignments": assignments,
            "epoch": {
                "epoch": 1,
                "coordinator_id": "coord",
                "fencing_token": "fence",
                "issued_at": 0.0,
                "lease_expires_at": 100.0,
            },
        }

    def test_malformed_promotion_epochs_fail_closed_on_load(self) -> None:
        document = self._document([{"node_id": "coord", "roles": ["coordinator"]}])
        document["promotion_epochs"] = [True, -1]
        with self.assertRaises(ValueError):
            role_state_from_dict(document)

    def test_contradictory_roles_fail_closed_on_load(self) -> None:
        with self.assertRaises(ValueError):
            role_state_from_dict(
                self._document(
                    [
                        {
                            "node_id": "coord",
                            "roles": ["coordinator", "subcoordinator"],
                        }
                    ]
                )
            )

    def test_stale_former_coordinator_state_remains_constructible(self) -> None:
        # Worker-only joined state and stale-former-coordinator state are both
        # legitimate; authority is decided by the active-epoch predicate.
        state = ClusterState(
            local_node_id="old",
            role_assignments=(
                RoleAssignment(frozenset({ClusterRole.COORDINATOR}), NodeId("old")),
            ),
            coordinator_epoch=CoordinatorEpoch(2, NodeId("new"), "fence", 0.0, 100.0),
        )
        self.assertFalse(state.is_active_coordinator)


if __name__ == "__main__":
    unittest.main()

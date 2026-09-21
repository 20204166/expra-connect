"""Pure role, capability, lease, and promotion decisions."""

from __future__ import annotations

import hashlib
import hmac
import secrets
from dataclasses import dataclass, replace

from ..identity import NodeId
from ..models import NodePermission
from .models import (
    CapabilityGrant,
    ClusterRole,
    CoordinatorEpoch,
    PromotionDecision,
    RoleAssignment,
    RoleChange,
)

HEARTBEAT_TIMEOUT_SECONDS = 120.0


class RoleAuthorizationError(ValueError):
    pass


class FencingError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class RoleState:
    assignments: tuple[RoleAssignment, ...] = ()
    epoch: CoordinatorEpoch | None = None
    promotion_epochs: frozenset[int] = frozenset()
    capability_grants: tuple[CapabilityGrant, ...] = ()

    def __post_init__(self) -> None:
        # Unbound assignments are valid intermediate values; only persisted or
        # addressed node assignments participate in cluster invariants.
        assignments = tuple(
            item for item in self.assignments if item.node_id is not None
        )
        ids = [item.node_id for item in assignments]
        if len(set(ids)) != len(ids):
            raise ValueError("invalid or duplicate role assignment")
        if (
            sum(
                not a.revoked and ClusterRole.COORDINATOR in a.roles
                for a in assignments
            )
            > 1
        ):
            raise ValueError("only one active Coordinator is allowed")
        if (
            sum(
                not a.revoked and ClusterRole.SUBCOORDINATOR in a.roles
                for a in assignments
            )
            > 1
        ):
            raise ValueError("only one active Subcoordinator is allowed")
        keys = [(g.subject, g.target) for g in self.capability_grants]
        if len(keys) != len(set(keys)):
            raise ValueError("duplicate capability grant")

    def assignment_for(self, node_id: NodeId) -> RoleAssignment | None:
        return next((a for a in self.assignments if a.node_id == node_id), None)

    def active_coordinator(self) -> RoleAssignment | None:
        return next(
            (
                a
                for a in self.assignments
                if (
                    a.node_id is not None
                    and not a.revoked
                    and ClusterRole.COORDINATOR in a.roles
                )
            ),
            None,
        )

    def capability_grant(
        self, subject: NodeId, target: NodeId, *, now: float | None = None
    ) -> CapabilityGrant | None:
        grant = next(
            (
                g
                for g in self.capability_grants
                if (g.subject, g.target) == (subject, target)
            ),
            None,
        )
        return (
            None
            if grant is not None and now is not None and now >= grant.expires_at
            else grant
        )

    def _control(self, actor: RoleAssignment) -> None:
        if actor.revoked or actor.paused or ClusterRole.COORDINATOR not in actor.roles:
            raise RoleAuthorizationError("only an active Coordinator may control peers")

    def _replace(self, assignment: RoleAssignment) -> RoleState:
        return replace(
            self,
            assignments=tuple(
                assignment if a.node_id == assignment.node_id else a
                for a in self.assignments
            ),
        )

    def assign(
        self,
        *,
        actor: RoleAssignment,
        target: NodeId,
        roles: frozenset[ClusterRole],
        now: float = 0.0,
    ) -> tuple[RoleState, RoleChange]:
        self._control(actor)
        if ClusterRole.COORDINATOR in roles:
            raise RoleAuthorizationError("Coordinator ownership cannot be delegated")
        current = self.assignment_for(target)
        if current is not None and current.revoked:
            raise RoleAuthorizationError("revoked node requires a new pairing invite")
        if ClusterRole.SUBCOORDINATOR in roles and any(
            a.node_id != target
            and not a.revoked
            and ClusterRole.SUBCOORDINATOR in a.roles
            for a in self.assignments
        ):
            raise RoleAuthorizationError("only one active Subcoordinator is allowed")
        assignment = RoleAssignment(
            roles, target, has_active_job=current.has_active_job if current else False
        )
        assignments = tuple(
            assignment if a.node_id == target else a for a in self.assignments
        )
        if current is None:
            assignments += (assignment,)
        return replace(self, assignments=assignments), RoleChange(
            target, assignment, now, actor.node_id or NodeId("actor")
        )

    def pause(self, *, actor: RoleAssignment, target: NodeId) -> RoleState:
        self._control(actor)
        current = self.assignment_for(target)
        if current is None or current.revoked:
            raise RoleAuthorizationError("unknown or revoked node")
        return self._replace(replace(current, paused=True))

    def resume(self, *, actor: RoleAssignment, target: NodeId) -> RoleState:
        self._control(actor)
        current = self.assignment_for(target)
        if current is None or current.revoked:
            raise RoleAuthorizationError("unknown or revoked node")
        return self._replace(replace(current, paused=False))

    def revoke(self, *, actor: RoleAssignment, target: NodeId) -> RoleState:
        self._control(actor)
        current = self.assignment_for(target)
        if current is None or current.revoked:
            raise RoleAuthorizationError("unknown or revoked node")
        updated = self._replace(replace(current, revoked=True, has_active_job=False))
        return replace(
            updated,
            capability_grants=tuple(
                g
                for g in updated.capability_grants
                if target not in (g.subject, g.target)
            ),
        )

    def clear_revocation(self, target: NodeId) -> RoleState:
        current = self.assignment_for(target)
        return (
            self
            if current is None or not current.revoked
            else replace(
                self,
                assignments=tuple(a for a in self.assignments if a.node_id != target),
            )
        )

    def _job(self, actor: RoleAssignment, target: NodeId, active: bool) -> RoleState:
        self._control(actor)
        current = self.assignment_for(target)
        if (
            current is None
            or current.revoked
            or ClusterRole.WORKER not in current.roles
        ):
            raise RoleAuthorizationError("target has no active worker role")
        return self._replace(replace(current, has_active_job=active))

    def assign_job(self, *, actor: RoleAssignment, target: NodeId) -> RoleState:
        return self._job(actor, target, True)

    def remove_job(self, *, actor: RoleAssignment, target: NodeId) -> RoleState:
        return self._job(actor, target, False)

    def grant_capabilities(
        self,
        *,
        actor: RoleAssignment,
        subject: NodeId,
        target: NodeId,
        permissions: frozenset[NodePermission],
        now: float,
        expires_at: float,
    ) -> RoleState:
        self._control(actor)
        sub = self.assignment_for(subject)
        worker = self.assignment_for(target)
        if sub is None or sub.revoked or ClusterRole.SUBCOORDINATOR not in sub.roles:
            raise RoleAuthorizationError(
                "capability subject is not an active Subcoordinator"
            )
        if (
            worker is None
            or worker.revoked
            or ClusterRole.WORKER not in worker.roles
            or subject == target
        ):
            raise RoleAuthorizationError("capability target is not an active Worker")
        grant = CapabilityGrant(subject, target, permissions, now, expires_at)
        return replace(
            self,
            capability_grants=tuple(
                g
                for g in self.capability_grants
                if (g.subject, g.target) != (subject, target)
            )
            + (grant,),
        )

    def revoke_capabilities(
        self, *, actor: RoleAssignment, subject: NodeId, target: NodeId
    ) -> RoleState:
        self._control(actor)
        return replace(
            self,
            capability_grants=tuple(
                g
                for g in self.capability_grants
                if (g.subject, g.target) != (subject, target)
            ),
        )

    def allows(
        self, *, subject: NodeId, target: NodeId, permission: NodePermission, now: float
    ) -> bool:
        source = self.assignment_for(subject)
        destination = self.assignment_for(target)
        if (
            source is None
            or destination is None
            or source.revoked
            or source.paused
            or destination.revoked
        ):
            return False
        if subject == target:
            return True
        if ClusterRole.COORDINATOR in source.roles:
            return ClusterRole.WORKER in destination.roles
        grant = self.capability_grant(subject, target, now=now)
        return (
            ClusterRole.SUBCOORDINATOR in source.roles
            and grant is not None
            and permission in grant.permissions
        )


def new_fencing_token() -> str:
    return secrets.token_hex(32)


def hash_invite(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def renew_lease(
    epoch: CoordinatorEpoch,
    *,
    coordinator_id: NodeId,
    fencing_token: str,
    now: float,
    lease_seconds: float = HEARTBEAT_TIMEOUT_SECONDS,
) -> CoordinatorEpoch:
    if epoch.coordinator_id != coordinator_id or not hmac.compare_digest(
        epoch.fencing_token, fencing_token
    ):
        raise FencingError("heartbeat fencing token is stale")
    if now > epoch.lease_expires_at:
        raise FencingError("coordinator lease has expired")
    return replace(
        epoch,
        issued_at=now,
        lease_expires_at=max(epoch.lease_expires_at, now + lease_seconds),
    )


def can_promote(
    state: RoleState,
    *,
    subcoordinator_id: NodeId,
    now: float,
    authenticated: bool = True,
) -> bool:
    a = state.assignment_for(subcoordinator_id)
    return bool(
        authenticated
        and state.epoch is not None
        and now >= state.epoch.lease_expires_at
        and a
        and not a.revoked
        and not a.paused
        and ClusterRole.SUBCOORDINATOR in a.roles
        and state.epoch.epoch not in state.promotion_epochs
    )


def promote_subcoordinator(
    state: RoleState, *, now: float, authenticated: bool = True
) -> PromotionDecision:
    if state.epoch is None:
        raise FencingError("no coordinator epoch exists")
    sub = next(
        (
            a
            for a in state.assignments
            if (
                a.node_id is not None
                and not a.revoked
                and not a.paused
                and ClusterRole.SUBCOORDINATOR in a.roles
            )
        ),
        None,
    )
    if (
        sub is None
        or sub.node_id is None
        or not can_promote(
            state, subcoordinator_id=sub.node_id, now=now, authenticated=authenticated
        )
    ):
        raise FencingError("subcoordinator cannot promote")
    epoch = CoordinatorEpoch(
        state.epoch.epoch + 1,
        sub.node_id,
        new_fencing_token(),
        now,
        now + HEARTBEAT_TIMEOUT_SECONDS,
    )
    promoted = RoleAssignment(
        frozenset({ClusterRole.COORDINATOR, ClusterRole.WORKER}), sub.node_id
    )
    assignments = tuple(
        promoted
        if a.node_id == sub.node_id
        else replace(a, roles=frozenset({ClusterRole.WORKER}))
        for a in state.assignments
    )
    return PromotionDecision(promoted, epoch, assignments)


def rejoin_as_worker(
    state: RoleState, *, node_id: NodeId, current_epoch: int
) -> RoleState:
    if state.epoch is not None and current_epoch < state.epoch.epoch:
        raise FencingError("returning node presented a stale epoch")
    a = state.assignment_for(node_id)
    return (
        state
        if a is None or ClusterRole.COORDINATOR not in a.roles
        else state._replace(
            replace(
                a,
                roles=frozenset({ClusterRole.WORKER}),
                paused=False,
                has_active_job=False,
            )
        )
    )

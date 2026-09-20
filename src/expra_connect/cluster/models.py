"""Immutable values shared by the cluster state machine."""

from __future__ import annotations

import math
from dataclasses import dataclass
from enum import Enum

from ..identity import NodeId
from ..models import NodePermission


class ClusterRole(str, Enum):
    WORKER = "worker"
    COORDINATOR = "coordinator"
    SUBCOORDINATOR = "subcoordinator"


@dataclass(frozen=True, slots=True)
class RoleAssignment:
    roles: frozenset[ClusterRole]
    node_id: NodeId | None = None
    paused: bool = False
    revoked: bool = False
    has_active_job: bool = False

    def __post_init__(self) -> None:
        roles = frozenset(ClusterRole(role) for role in self.roles)
        if ClusterRole.COORDINATOR in roles:
            roles |= {ClusterRole.WORKER}
        if not roles:
            roles = frozenset({ClusterRole.WORKER})
        if self.revoked and self.paused:
            raise ValueError("a revoked node cannot also be paused")
        object.__setattr__(self, "roles", roles)


@dataclass(frozen=True, slots=True)
class CoordinatorEpoch:
    epoch: int
    coordinator_id: NodeId
    fencing_token: str
    issued_at: float = 0.0
    lease_expires_at: float = 0.0

    def __post_init__(self) -> None:
        if (
            type(self.epoch) is not int
            or self.epoch < 0
            or not self.fencing_token
            or not math.isfinite(self.issued_at)
            or not math.isfinite(self.lease_expires_at)
            or self.lease_expires_at < self.issued_at
        ):
            raise ValueError("invalid coordinator epoch")


@dataclass(frozen=True, slots=True)
class CoordinatorLease:
    coordinator_id: NodeId
    epoch: int
    fencing_token: str
    issued_at: float
    expires_at: float
    last_heartbeat_at: float

    def __post_init__(self) -> None:
        if type(self.epoch) is not int or self.epoch < 0 or not self.fencing_token:
            raise ValueError("invalid coordinator lease")
        if (
            not math.isfinite(self.issued_at)
            or not math.isfinite(self.expires_at)
            or self.expires_at < self.issued_at
        ):
            raise ValueError("lease expiry precedes issuance")


@dataclass(frozen=True, slots=True)
class CapabilityGrant:
    subject: NodeId
    target: NodeId
    permissions: frozenset[NodePermission]
    issued_at: float
    expires_at: float

    def __post_init__(self) -> None:
        if (
            not self.permissions
            or not math.isfinite(self.issued_at)
            or not math.isfinite(self.expires_at)
        ):
            raise ValueError("invalid capability grant")
        if self.expires_at <= self.issued_at:
            raise ValueError("capability grant must expire after issuance")


@dataclass(frozen=True, slots=True)
class RoleChange:
    node_id: NodeId
    assignment: RoleAssignment
    changed_at: float
    actor_id: NodeId


@dataclass(frozen=True, slots=True)
class PromotionDecision:
    role_assignment: RoleAssignment
    epoch: CoordinatorEpoch
    assignments: tuple[RoleAssignment, ...]

    @property
    def role(self) -> ClusterRole:
        return ClusterRole.COORDINATOR


@dataclass(frozen=True, slots=True)
class InviteRecord:
    token_hash: str
    target_node_id: str
    expires_at: float
    token: str = ""
    cluster_id: str = ""
    coordinator_id: str = ""
    epoch: int = 0
    fencing_token: str = ""

    def __post_init__(self) -> None:
        if not self.token_hash or not math.isfinite(self.expires_at):
            raise ValueError("invalid invite")

    @property
    def target_id(self) -> NodeId:
        """Legacy object-shaped access used by the runtime compatibility path."""
        return NodeId(self.target_node_id)

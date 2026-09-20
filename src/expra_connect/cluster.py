"""Transport-neutral cluster membership, invites, roles, and fencing."""

from __future__ import annotations

import hmac
import secrets
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum
from typing import Any

from .identity import NodeId
from .persistence import JsonStateStore


class ClusterRole(str, Enum):
    COORDINATOR = "coordinator"
    WORKER = "worker"


@dataclass(frozen=True, slots=True)
class RoleAssignment:
    node_id: NodeId
    role: ClusterRole
    revoked: bool = False


@dataclass(frozen=True, slots=True)
class CoordinatorEpoch:
    epoch: int
    coordinator_id: NodeId
    fencing_token: str


@dataclass(frozen=True, slots=True)
class Invite:
    token: str
    target_id: NodeId
    cluster_id: str
    coordinator_id: NodeId
    epoch: CoordinatorEpoch
    expires_at: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "token": self.token,
            "target_id": self.target_id.value,
            "cluster_id": self.cluster_id,
            "coordinator_id": self.coordinator_id.value,
            "epoch": self.epoch.epoch,
            "fencing_token": self.epoch.fencing_token,
            "expires_at": self.expires_at,
        }


class Cluster:
    def __init__(
        self, local_id: NodeId, *, clock: Callable[[], float] = time.time
    ) -> None:
        self.local_id = local_id
        self.cluster_id = uuid.uuid4().hex
        self._clock = clock
        self.epoch = CoordinatorEpoch(1, local_id, secrets.token_hex(32))
        self.assignments: dict[NodeId, RoleAssignment] = {
            local_id: RoleAssignment(local_id, ClusterRole.COORDINATOR)
        }
        self._online: dict[NodeId, bool] = {local_id: True}
        self._used_invites: set[str] = set()
        self._invites: dict[str, Invite] = {}

    def to_dict(self) -> dict[str, Any]:
        return {
            "cluster_id": self.cluster_id,
            "local_id": str(self.local_id),
            "epoch": self.epoch.epoch,
            "coordinator_id": str(self.epoch.coordinator_id),
            "fencing_token": self.epoch.fencing_token,
            "assignments": [
                {
                    "node_id": str(item.node_id),
                    "role": item.role.value,
                    "revoked": item.revoked,
                }
                for item in self.assignments.values()
            ],
            "used_invites": sorted(self._used_invites),
        }

    def save(self, store: JsonStateStore) -> None:
        store.save(self.to_dict())

    @classmethod
    def load(
        cls,
        local_id: NodeId,
        store: JsonStateStore,
        *,
        clock: Callable[[], float] = time.time,
    ) -> Cluster:
        value = store.load()
        try:
            cluster = cls(local_id, clock=clock)
            if value.get("local_id") != local_id.value:
                raise ValueError("persisted cluster belongs to another local node")
            cluster.cluster_id = str(value["cluster_id"])
            raw_epoch = value["epoch"]
            if isinstance(raw_epoch, bool) or not isinstance(raw_epoch, int):
                raise TypeError("persisted epoch is invalid")
            cluster.epoch = CoordinatorEpoch(
                raw_epoch,
                NodeId(str(value["coordinator_id"])),
                str(value["fencing_token"]),
            )
            assignments: dict[NodeId, RoleAssignment] = {}
            for item in value["assignments"]:
                node_id = NodeId(str(item["node_id"]))
                assignments[node_id] = RoleAssignment(
                    node_id,
                    ClusterRole(str(item["role"])),
                    bool(item.get("revoked", False)),
                )
            if local_id not in assignments:
                raise ValueError("persisted cluster omits local node")
            cluster.assignments = assignments
            cluster._online = {node_id: False for node_id in assignments}
            used_invites = value.get("used_invites", [])
            if not isinstance(used_invites, list) or any(
                not isinstance(item, str) for item in used_invites
            ):
                raise TypeError("persisted invite records are invalid")
            cluster._used_invites = set(used_invites)
            return cluster
        except (KeyError, TypeError, ValueError) as error:
            raise ValueError("persisted cluster state is invalid") from error

    @property
    def coordinator_id(self) -> NodeId:
        return self.epoch.coordinator_id

    @property
    def local_role(self) -> ClusterRole:
        assignment = self.assignments.get(self.local_id)
        if assignment is None or assignment.revoked:
            raise ValueError("local node has no active role")
        return assignment.role

    def create_invite(self, target_id: NodeId, *, now: float | None = None) -> Invite:
        if self.coordinator_id != self.local_id:
            raise PermissionError("only the Coordinator may create invites")
        current = self._clock() if now is None else now
        invite = Invite(
            secrets.token_urlsafe(24),
            target_id,
            self.cluster_id,
            self.coordinator_id,
            self.epoch,
            current + 300,
        )
        self._invites[invite.token] = invite
        return invite

    def join(
        self, invite: Invite, coordinator: Cluster, *, now: float | None = None
    ) -> None:
        current = self._clock() if now is None else now
        if invite.token in coordinator._used_invites or invite.expires_at <= current:
            raise ValueError("invite is expired or already consumed")
        if (
            invite.target_id != self.local_id
            or invite.cluster_id != coordinator.cluster_id
            or invite.epoch != coordinator.epoch
        ):
            raise ValueError("invite target or cluster mismatch")
        coordinator._invites.pop(invite.token, None)
        coordinator._used_invites.add(invite.token)
        coordinator.assign(self.local_id, ClusterRole.WORKER)
        self.cluster_id = coordinator.cluster_id
        self.epoch = coordinator.epoch
        self.assignments = {
            self.local_id: RoleAssignment(self.local_id, ClusterRole.WORKER),
            coordinator.local_id: RoleAssignment(
                coordinator.local_id, ClusterRole.COORDINATOR
            ),
        }
        self._online[coordinator.local_id] = True

    def consume_invite(
        self, token: str, target_id: NodeId, *, now: float | None = None
    ) -> Invite:
        """Consume a locally issued invite and admit its target as a worker."""

        if self.coordinator_id != self.local_id:
            raise PermissionError("only the Coordinator may consume invites")
        invite = self._invites.get(token)
        current = self._clock() if now is None else now
        if (
            invite is None
            or token in self._used_invites
            or invite.expires_at <= current
        ):
            raise ValueError("invite is expired or already consumed")
        if invite.target_id != target_id or invite.cluster_id != self.cluster_id:
            raise ValueError("invite target or cluster mismatch")
        self._invites.pop(token)
        self._used_invites.add(token)
        self.assign(target_id, ClusterRole.WORKER)
        return invite

    def assign(self, node_id: NodeId, role: ClusterRole) -> None:
        if role is ClusterRole.COORDINATOR and any(
            item.role is ClusterRole.COORDINATOR
            and not item.revoked
            and item.node_id != node_id
            for item in self.assignments.values()
        ):
            raise ValueError("cluster cannot have two active coordinators")
        self.assignments[node_id] = RoleAssignment(node_id, role)
        self._online.setdefault(node_id, True)

    def is_member(self, node_id: NodeId) -> bool:
        assignment = self.assignments.get(node_id)
        return assignment is not None and not assignment.revoked

    def set_online(self, node_id: NodeId, online: bool) -> None:
        self._online[node_id] = online

    def online(self, node_id: NodeId) -> bool:
        return self._online.get(node_id, False)

    def authorize(self, *, cluster_id: str, epoch: int, fencing_token: str) -> None:
        if cluster_id != self.cluster_id:
            raise PermissionError("cluster identity mismatch")
        if epoch != self.epoch.epoch or not hmac.compare_digest(
            fencing_token, self.epoch.fencing_token
        ):
            raise PermissionError("stale coordinator authority")

    def revoke(self, node_id: NodeId) -> None:
        assignment = self.assignments.get(node_id)
        if assignment is not None:
            self.assignments[node_id] = RoleAssignment(
                node_id, assignment.role, revoked=True
            )

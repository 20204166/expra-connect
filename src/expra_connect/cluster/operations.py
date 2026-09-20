"""Mutable cluster facade and strict persisted cluster state."""

from __future__ import annotations

import hashlib
import hmac
import math
import secrets
import time
from collections.abc import Callable
from dataclasses import replace
from typing import Any

from ..identity import NodeId
from .invites import ClusterDataError, create_invite
from .models import ClusterRole, CoordinatorEpoch, InviteRecord, RoleAssignment
from .roles import RoleState, hash_invite, new_fencing_token
from .state import role_state_from_dict, role_state_to_dict


class ClusterState:
    def __init__(
        self,
        *,
        local_node_id: str = "local",
        cluster_id: str = "local-cluster",
        role_assignments: tuple[RoleAssignment, ...] = (),
        coordinator_epoch: CoordinatorEpoch | None = None,
        active_invites: tuple[InviteRecord, ...] = (),
        join_admissions: tuple[InviteRecord, ...] = (),
        used_invites: frozenset[str] = frozenset(),
        promotion_epochs: frozenset[int] = frozenset(),
        capability_grants: tuple[Any, ...] = (),
    ) -> None:
        self.local_node_id = local_node_id
        self.cluster_id = cluster_id
        self.role_assignments = role_assignments
        self.coordinator_epoch = coordinator_epoch
        self.active_invites = active_invites
        self.join_admissions = join_admissions
        self.used_invites = frozenset(used_invites)
        self.promotion_epochs = promotion_epochs
        self.capability_grants = capability_grants

    @classmethod
    def create_local(
        cls, *, local_node_id: str | None = None, now: float | None = None
    ) -> ClusterState:
        node = local_node_id or secrets.token_hex(16)
        current = time.time() if now is None else now
        assignment = RoleAssignment(
            frozenset({ClusterRole.COORDINATOR, ClusterRole.WORKER}), NodeId(node)
        )
        return cls(
            local_node_id=node,
            cluster_id=secrets.token_urlsafe(18),
            role_assignments=(assignment,),
            coordinator_epoch=CoordinatorEpoch(
                1, NodeId(node), new_fencing_token(), current, current + 120.0
            ),
        )

    @property
    def local_assignment(self) -> RoleAssignment:
        return next(
            (
                a
                for a in self.role_assignments
                if a.node_id and a.node_id.value == self.local_node_id
            ),
            RoleAssignment(frozenset({ClusterRole.WORKER}), NodeId(self.local_node_id)),
        )

    @property
    def is_active_coordinator(self) -> bool:
        return bool(
            self.coordinator_epoch
            and self.coordinator_epoch.coordinator_id.value == self.local_node_id
            and not self.local_assignment.paused
            and not self.local_assignment.revoked
            and ClusterRole.COORDINATOR in self.local_assignment.roles
        )

    def create_invite(
        self,
        *,
        target_node_id: str = "",
        now: float | None = None,
        ttl_seconds: float = 300.0,
    ) -> InviteRecord:
        local = self.local_assignment
        if (
            local.revoked
            or local.paused
            or ClusterRole.COORDINATOR not in local.roles
            or self.coordinator_epoch is None
            or self.coordinator_epoch.coordinator_id.value != self.local_node_id
        ):
            raise PermissionError("only an active local Coordinator may create invites")
        if self.coordinator_epoch is None:
            raise ValueError("cluster has no coordinator epoch to bind an invite to")
        invite = create_invite(
            cluster_id=self.cluster_id,
            target_node_id=target_node_id,
            epoch=self.coordinator_epoch,
            now=time.time() if now is None else now,
            ttl_seconds=ttl_seconds,
        )
        self.active_invites += (invite,)
        return invite

    def consume_invite(self, token: str, *, now: float | None = None) -> InviteRecord:
        local = self.local_assignment
        if (
            local.revoked
            or local.paused
            or ClusterRole.COORDINATOR not in local.roles
            or self.coordinator_epoch is None
            or self.coordinator_epoch.coordinator_id.value != self.local_node_id
        ):
            raise PermissionError(
                "only an active local Coordinator may consume invites"
            )
        digest = hash_invite(token)
        current = time.time() if now is None else now
        for invite in self.active_invites:
            if invite.token_hash != digest:
                continue
            self.active_invites = tuple(
                item for item in self.active_invites if item != invite
            )
            self.used_invites = self.used_invites | {digest}
            if current >= invite.expires_at:
                raise ValueError("pairing invite has expired")
            return invite
        if digest in self.used_invites:
            raise ValueError("pairing invite is expired or already consumed")
        raise ValueError("pairing invite is unknown or expired")

    def to_dict(self) -> dict[str, Any]:
        state = RoleState(
            self.role_assignments,
            self.coordinator_epoch,
            self.promotion_epochs,
            tuple(self.capability_grants),
        )
        value = role_state_to_dict(state)
        value["assignments"] = [
            dict(item, role=item["roles"][0]) for item in value["assignments"]
        ]
        value.update(
            {
                "schema_version": 2,
                "cluster_id": self.cluster_id,
                "local_node_id": self.local_node_id,
                "local_id": self.local_node_id,
                "active_invites": [
                    {
                        "token_hash": item.token_hash,
                        "target_node_id": item.target_node_id,
                        "expires_at": item.expires_at,
                        "cluster_id": item.cluster_id,
                        "coordinator_id": item.coordinator_id,
                        "epoch": item.epoch,
                        "fencing_token": item.fencing_token,
                    }
                    for item in self.active_invites
                ],
                "join_admissions": [
                    {
                        "token_hash": item.token_hash,
                        "target_node_id": item.target_node_id,
                        "expires_at": item.expires_at,
                        "cluster_id": item.cluster_id,
                        "coordinator_id": item.coordinator_id,
                        "epoch": item.epoch,
                        "fencing_token": item.fencing_token,
                    }
                    for item in self.join_admissions
                ],
                "used_invites": sorted(self.used_invites),
            }
        )
        return value

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> ClusterState:
        if not isinstance(value, dict) or value.get("schema_version", 2) != 2:
            raise ClusterDataError("cluster state is malformed")
        raw = dict(value)
        if "assignments" not in raw and "role_assignments" in raw:
            raw["assignments"] = raw["role_assignments"]
        if type(raw.get("epoch")) is int:
            raw["epoch"] = {
                "epoch": raw["epoch"],
                "coordinator_id": raw.get("coordinator_id"),
                "fencing_token": raw.get("fencing_token"),
                "issued_at": 0.0,
                "lease_expires_at": 0.0,
            }
        role_state = role_state_from_dict(raw)
        local = value.get("local_node_id", value.get("local_id", "local"))
        cluster_id = value.get("cluster_id", "local-cluster")
        if (
            not isinstance(local, str)
            or not local
            or not isinstance(cluster_id, str)
            or not cluster_id
        ):
            raise ClusterDataError("cluster identity is malformed")
        raw_invites = value.get("active_invites", [])

        def parse_invites(raw_items: object, field: str) -> list[InviteRecord]:
            if not isinstance(raw_items, list):
                raise ClusterDataError(f"{field} are malformed")
            parsed: list[InviteRecord] = []
            for item in raw_items:
                if not isinstance(item, dict):
                    raise ClusterDataError(f"{field[:-1]} is malformed")
                expiry, epoch = item.get("expires_at"), item.get("epoch")
                if (
                    not isinstance(item.get("token_hash"), str)
                    or not item["token_hash"]
                    or not isinstance(item.get("target_node_id", ""), str)
                    or not isinstance(item.get("cluster_id", ""), str)
                    or not isinstance(item.get("coordinator_id", ""), str)
                    or not isinstance(item.get("fencing_token", ""), str)
                    or type(epoch) is not int
                    or epoch < 0
                    or not isinstance(expiry, (int, float))
                    or isinstance(expiry, bool)
                    or not math.isfinite(float(expiry))
                ):
                    raise ClusterDataError(f"{field[:-1]} is malformed")
                parsed.append(
                    InviteRecord(
                        item["token_hash"],
                        item.get("target_node_id", ""),
                        float(expiry),
                        cluster_id=item.get("cluster_id", ""),
                        coordinator_id=item.get("coordinator_id", ""),
                        epoch=epoch,
                        fencing_token=item.get("fencing_token", ""),
                    )
                )
            return parsed

        invites = parse_invites(raw_invites, "active invites")
        admissions = parse_invites(value.get("join_admissions", []), "join admissions")
        raw_used = value.get("used_invites", [])
        if not isinstance(raw_used, list) or any(
            not isinstance(item, str) or not item for item in raw_used
        ):
            raise ClusterDataError("used invites are malformed")
        used = frozenset(
            item
            if len(item) == 64 and all(c in "0123456789abcdef" for c in item)
            else hashlib.sha256(item.encode()).hexdigest()
            for item in raw_used
        )
        return cls(
            local_node_id=local,
            cluster_id=cluster_id,
            role_assignments=role_state.assignments,
            coordinator_epoch=role_state.epoch,
            active_invites=tuple(invites),
            join_admissions=tuple(admissions),
            used_invites=used,
            promotion_epochs=role_state.promotion_epochs,
            capability_grants=role_state.capability_grants,
        )


class Cluster:
    def __init__(
        self, local_id: NodeId, *, clock: Callable[[], float] = time.time
    ) -> None:
        self.local_id, self._clock = local_id, clock
        state = ClusterState.create_local(local_node_id=local_id.value, now=clock())
        if state.coordinator_epoch is None:
            raise ValueError("local cluster has no coordinator epoch")
        self.cluster_id, self.epoch = state.cluster_id, state.coordinator_epoch
        self.assignments = {local_id: state.local_assignment}
        self._online: dict[NodeId, bool] = {local_id: True}
        self._invites: dict[str, InviteRecord] = {}
        self._join_admissions: dict[str, InviteRecord] = {}
        self._used_invites = set(state.used_invites)
        self.promotion_epochs = frozenset(state.promotion_epochs)
        self.capability_grants = tuple(state.capability_grants)

    @property
    def coordinator_id(self) -> NodeId:
        return self.epoch.coordinator_id

    @property
    def is_active_coordinator(self) -> bool:
        assignment = self.assignments.get(self.local_id)
        return bool(
            assignment is not None
            and not assignment.paused
            and not assignment.revoked
            and ClusterRole.COORDINATOR in assignment.roles
            and self.epoch.coordinator_id == self.local_id
        )

    @property
    def local_role(self) -> ClusterRole:
        assignment = self.assignments.get(self.local_id)
        if assignment is None or assignment.revoked:
            raise ValueError("local node has no active role")
        return (
            ClusterRole.COORDINATOR
            if ClusterRole.COORDINATOR in assignment.roles
            else ClusterRole.WORKER
        )

    @property
    def local_assignment(self) -> RoleAssignment:
        return self.assignments.get(
            self.local_id,
            RoleAssignment(frozenset({ClusterRole.WORKER}), self.local_id),
        )

    def create_invite(
        self, target_id: NodeId, *, now: float | None = None
    ) -> InviteRecord:
        if not self.is_active_coordinator:
            raise PermissionError("only an active local Coordinator may create invites")
        invite = create_invite(
            cluster_id=self.cluster_id,
            target_node_id=target_id.value,
            epoch=self.epoch,
            now=self._clock() if now is None else now,
        )
        self._invites[invite.token] = invite
        return invite

    def _restore_invites(self, invites: tuple[InviteRecord, ...]) -> None:
        self._invites.update({item.token_hash: item for item in invites})

    def assign(self, node_id: NodeId, role: ClusterRole) -> None:
        if not self.is_active_coordinator:
            raise PermissionError("only an active local Coordinator may assign roles")
        if role is ClusterRole.COORDINATOR and any(
            not item.revoked and ClusterRole.COORDINATOR in item.roles
            for key, item in self.assignments.items()
            if key != node_id
        ):
            raise ValueError("cluster cannot have two active coordinators")
        self.assignments[node_id] = RoleAssignment(frozenset({role}), node_id)
        self._online.setdefault(node_id, True)

    def consume_invite(
        self,
        token: str,
        target_id: NodeId,
        *,
        now: float | None = None,
        allow_retry: bool = False,
    ) -> InviteRecord:
        if not self.is_active_coordinator:
            raise PermissionError(
                "only an active local Coordinator may consume invites"
            )
        digest = hash_invite(token)
        invite = self._invites.get(token) or self._invites.get(digest)
        current = self._clock() if now is None else now
        admission = self._join_admissions.get(digest)
        if admission is not None and allow_retry:
            if admission.target_id != target_id:
                raise ValueError("invite target mismatch")
            return admission
        if admission is not None:
            raise ValueError("invite is expired or already consumed")
        if (
            invite is None
            or digest in self._used_invites
            or current >= invite.expires_at
        ):
            raise ValueError("invite is expired or already consumed")
        if (
            invite.target_id != target_id
            or invite.cluster_id != self.cluster_id
            or invite.epoch != self.epoch.epoch
        ):
            raise ValueError("invite target or cluster mismatch")
        self._invites.pop(token, None)
        self._invites.pop(digest, None)
        self._used_invites.add(digest)
        self.assign(target_id, ClusterRole.WORKER)
        self._join_admissions[digest] = replace(invite, token="")
        return invite

    def join(
        self,
        invite: InviteRecord,
        coordinator: Cluster,
        *,
        pairing: Any,
        now: float | None = None,
    ) -> None:
        admission = getattr(pairing, "can_join_cluster", pairing)
        if not callable(admission) or not admission(coordinator.local_id):
            raise PermissionError("cluster Join requires an authenticated Pair")
        if self.local_assignment.revoked or self.local_assignment.paused:
            raise PermissionError("paused or revoked local membership cannot join")
        if self.cluster_id == coordinator.cluster_id and self.is_member(self.local_id):
            raise ValueError("local node has already joined this cluster")
        coordinator.consume_invite(
            invite.token, self.local_id, now=now, allow_retry=True
        )
        self.cluster_id = coordinator.cluster_id
        self.epoch = coordinator.epoch
        self.assignments = {
            self.local_id: RoleAssignment(
                frozenset({ClusterRole.WORKER}), self.local_id
            ),
            coordinator.local_id: RoleAssignment(
                frozenset({ClusterRole.COORDINATOR, ClusterRole.WORKER}),
                coordinator.local_id,
            ),
        }
        self._online[coordinator.local_id] = True

    def to_dict(self) -> dict[str, Any]:
        return ClusterState(
            local_node_id=self.local_id.value,
            cluster_id=self.cluster_id,
            role_assignments=tuple(self.assignments.values()),
            coordinator_epoch=self.epoch,
            active_invites=tuple(self._invites.values()),
            join_admissions=tuple(self._join_admissions.values()),
            used_invites=frozenset(self._used_invites),
            promotion_epochs=self.promotion_epochs,
            capability_grants=self.capability_grants,
        ).to_dict()

    def save(self, store: Any) -> None:
        store.save(self.to_dict())

    @classmethod
    def load(
        cls, local_id: NodeId, store: Any, *, clock: Callable[[], float] = time.time
    ) -> Cluster:
        state = ClusterState.from_dict(store.load())
        if state.local_node_id != local_id.value or local_id not in (
            a.node_id for a in state.role_assignments
        ):
            raise ValueError("persisted cluster belongs to another local node")
        result = cls(local_id, clock=clock)
        if state.coordinator_epoch is None:
            raise ValueError("persisted cluster has no coordinator epoch")
        result.cluster_id, result.epoch = state.cluster_id, state.coordinator_epoch
        result.assignments = {a.node_id: a for a in state.role_assignments if a.node_id}
        result._used_invites = set(state.used_invites)
        result.promotion_epochs = frozenset(state.promotion_epochs)
        result.capability_grants = tuple(state.capability_grants)
        result._restore_invites(state.active_invites)
        result._join_admissions = {
            item.token_hash: item for item in state.join_admissions
        }
        return result

    def is_member(self, node_id: NodeId) -> bool:
        return node_id in self.assignments and not self.assignments[node_id].revoked

    def set_online(self, node_id: NodeId, online: bool) -> None:
        self._online[node_id] = online

    def online(self, node_id: NodeId) -> bool:
        return self._online.get(node_id, False)

    def revoke(self, node_id: NodeId) -> None:
        if not self.is_active_coordinator:
            raise PermissionError("only an active local Coordinator may revoke roles")
        if node_id in self.assignments:
            self.assignments[node_id] = replace(self.assignments[node_id], revoked=True)
            self.capability_grants = tuple(
                grant
                for grant in self.capability_grants
                if node_id not in (grant.subject, grant.target)
            )

    def authorize(self, *, cluster_id: str, epoch: int, fencing_token: str) -> None:
        if (
            cluster_id != self.cluster_id
            or epoch != self.epoch.epoch
            or not hmac.compare_digest(fencing_token, self.epoch.fencing_token)
        ):
            raise PermissionError("stale coordinator authority")

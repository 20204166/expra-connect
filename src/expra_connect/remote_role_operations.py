"""Cluster role-operation adapter mixed into authenticated remote providers."""

from __future__ import annotations

import math
import time
from typing import Any, cast

from .cluster import ClusterRole
from .cluster.models import InviteRecord, RoleAssignment
from .cluster.roles import RoleState, renew_lease
from .identity import NodeId
from .models import NodePermission


class RuntimeClusterOperations:
    """Small runtime facade for canonical cluster lifecycle operations."""

    _cluster: Any
    _identity: Any
    _service: Any
    _cluster_capability_grants: tuple[Any, ...]

    def _save_persisted_state(self) -> bool:
        raise NotImplementedError

    def _require_pairing(self) -> Any:
        raise NotImplementedError

    def _hydrate_registry_membership(self) -> None:
        raise NotImplementedError

    def _refresh_live_grants(self) -> None:
        raise NotImplementedError

    def _persist_failover_state(self, state: RoleState) -> None:
        cluster = self._require_cluster()
        previous = (
            cluster.assignments,
            cluster.epoch,
            cluster.promotion_epochs,
            cluster.capability_grants,
            self._cluster_capability_grants,
        )
        cluster.assignments = {
            item.node_id: item for item in state.assignments if item.node_id is not None
        }
        cluster.epoch = state.epoch
        cluster.promotion_epochs = state.promotion_epochs
        cluster.capability_grants = state.capability_grants
        self._cluster_capability_grants = state.capability_grants
        if not self._save_persisted_state():
            (
                cluster.assignments,
                cluster.epoch,
                cluster.promotion_epochs,
                cluster.capability_grants,
                self._cluster_capability_grants,
            ) = previous
            raise RuntimeError("failover state was not durably persisted")
        if self._service is not None and cluster.epoch is not None:
            self._service.update_cluster_fence(
                cluster_id=cluster.cluster_id,
                coordinator_epoch=cluster.epoch.epoch,
                fencing_token=cluster.epoch.fencing_token,
            )
        self._hydrate_registry_membership()
        self._refresh_live_grants()

    def _require_failover(self) -> Any:
        manager = getattr(self, "_failover_coordinator", None)
        if manager is None:
            raise RuntimeError("cluster failover is unavailable")
        return manager

    def promote_if_due(self, *, now: float) -> bool:
        return bool(self._require_failover().promote_if_due(now=now))

    def heartbeat(
        self, *, coordinator_id: NodeId, fencing_token: str, now: float
    ) -> bool:
        return bool(
            self._require_failover().heartbeat(
                coordinator_id=coordinator_id, fencing_token=fencing_token, now=now
            )
        )

    def rejoin_as_worker(self, *, node_id: NodeId, current_epoch: int) -> None:
        self._require_failover().rejoin_as_worker(
            node_id=node_id, current_epoch=current_epoch
        )

    def _require_cluster(self) -> Any:
        if self._cluster is None:
            raise RuntimeError("cluster participation is disabled")
        return self._cluster

    def cluster_status(self) -> dict[str, Any]:
        cluster = self._cluster
        if cluster is None:
            return {"enabled": False, "members": []}
        return {
            "enabled": True,
            "cluster_id": cluster.cluster_id,
            "coordinator_id": cluster.coordinator_id.value,
            "epoch": cluster.epoch.epoch,
            "members": [
                {
                    "node_id": node.value,
                    "roles": sorted(role.value for role in assignment.roles),
                    "paused": assignment.paused,
                    "revoked": assignment.revoked,
                }
                for node, assignment in cluster.assignments.items()
            ],
        }

    def create_invite(
        self, target_node_id: NodeId, *, ttl_seconds: float = 300.0
    ) -> InviteRecord:
        cluster = self._require_cluster()
        if ttl_seconds != 300.0:
            raise ValueError("custom invite TTL is not supported")
        invite = cluster.create_invite(target_node_id)
        if not self._save_persisted_state():
            cluster._invites.pop(invite.token, None)
            raise RuntimeError("cluster invite was not durably persisted")
        return cast(InviteRecord, invite)

    def join_cluster(self, provider: Any, invite: InviteRecord) -> dict[str, Any]:
        cluster = self._require_cluster()
        pairing = self._require_pairing()
        peer_id = getattr(provider, "_caller_node_id", None) or getattr(
            provider, "_node_id", None
        )
        if not isinstance(peer_id, NodeId) or not pairing.can_join_cluster(peer_id):
            raise PermissionError("cluster Join requires an authenticated Pair")
        if self._identity is None:
            raise RuntimeError("identity is unavailable")
        response = provider.consume_invite(
            invite.token,
            cluster_id=invite.cluster_id,
            epoch=invite.epoch,
            fencing_token=invite.fencing_token,
        )
        if not isinstance(response, dict):
            raise TypeError("cluster Join response is malformed")
        response_epoch = response.get("epoch")
        response_expiry = response.get("expires_at")
        if (
            set(response)
            != {
                "target_node_id",
                "expires_at",
                "cluster_id",
                "coordinator_id",
                "epoch",
                "fencing_token",
            }
            or response.get("target_node_id") != self._identity.node_id.value
            or response.get("cluster_id") != invite.cluster_id
            or response.get("coordinator_id") != invite.coordinator_id
            or type(response_epoch) is not int
            or response_epoch < 0
            or response_epoch != invite.epoch
            or not isinstance(response.get("fencing_token"), str)
            or not response["fencing_token"]
            or response["fencing_token"] != invite.fencing_token
            or not isinstance(response_expiry, (int, float))
            or isinstance(response_expiry, bool)
            or not math.isfinite(float(response_expiry))
        ):
            raise ValueError("cluster Join response is malformed")
        if float(response_expiry) != float(invite.expires_at):
            raise ValueError("cluster Join response is malformed")
        previous = (
            cluster.cluster_id,
            cluster.epoch,
            dict(cluster.assignments),
            dict(cluster._online),
            dict(cluster._invites),
            dict(cluster._join_admissions),
            set(cluster._used_invites),
            cluster.promotion_epochs,
            cluster.capability_grants,
        )
        try:
            cluster.cluster_id = response["cluster_id"]
            cluster.epoch = type(cluster.epoch)(
                response["epoch"],
                NodeId(response["coordinator_id"]),
                response["fencing_token"],
                cluster.epoch.issued_at,
                cluster.epoch.lease_expires_at,
            )
            cluster.assignments = {
                self._identity.node_id: RoleAssignment(
                    frozenset({ClusterRole.WORKER}), self._identity.node_id
                )
            }
            cluster._online = {self._identity.node_id: True}
            cluster.promotion_epochs = frozenset()
            cluster.capability_grants = ()
            if not self._save_persisted_state():
                raise RuntimeError("cluster join was not durably persisted")
        except Exception:
            (
                cluster.cluster_id,
                cluster.epoch,
                cluster.assignments,
                cluster._online,
                cluster._invites,
                cluster._join_admissions,
                cluster._used_invites,
                cluster.promotion_epochs,
                cluster.capability_grants,
            ) = previous
            raise
        self._hydrate_registry_membership()
        return response


class RemoteRoleOperations:
    """Build fenced cluster requests without owning transport or authorization."""

    def consume_invite(
        self, token: str, *, cluster_id: str, epoch: int, fencing_token: str
    ) -> dict[str, Any]:
        return self._role_request(
            "consume_invite",
            {
                "token": token,
                "cluster_id": cluster_id,
                "epoch": epoch,
                "fencing_token": fencing_token,
            },
        )

    def assign_role(
        self,
        target_node_id: str,
        roles: list[str],
        *,
        cluster_id: str,
        epoch: int,
        fencing_token: str,
    ) -> dict[str, Any]:
        return self._role_request(
            "assign_role",
            {
                "target_node_id": target_node_id,
                "roles": roles,
                "cluster_id": cluster_id,
                "epoch": epoch,
                "fencing_token": fencing_token,
            },
        )

    def renew_coordinator_lease(
        self, *, cluster_id: str, epoch: int, fencing_token: str
    ) -> dict[str, Any]:
        return self._role_request(
            "renew_coordinator_lease",
            {
                "cluster_id": cluster_id,
                "epoch": epoch,
                "fencing_token": fencing_token,
            },
        )

    def upload_snapshot(
        self,
        payload: dict[str, Any],
        *,
        cluster_id: str,
        epoch: int,
        fencing_token: str,
    ) -> dict[str, Any]:
        return self._role_request(
            "worker_snapshot",
            {
                "payload": payload,
                "cluster_id": cluster_id,
                "epoch": epoch,
                "fencing_token": fencing_token,
            },
        )

    def upload_standby_batch(
        self,
        payload: dict[str, Any],
        *,
        cluster_id: str,
        epoch: int,
        fencing_token: str,
    ) -> dict[str, Any]:
        return self._role_request(
            "standby_batch",
            {
                "payload": payload,
                "cluster_id": cluster_id,
                "epoch": epoch,
                "fencing_token": fencing_token,
            },
        )

    def pause_worker(
        self, target_node_id: str, *, cluster_id: str, epoch: int, fencing_token: str
    ) -> dict[str, Any]:
        return self._role_request(
            "pause_worker",
            {
                "target_node_id": target_node_id,
                "cluster_id": cluster_id,
                "epoch": epoch,
                "fencing_token": fencing_token,
            },
        )

    def revoke_worker(
        self, target_node_id: str, *, cluster_id: str, epoch: int, fencing_token: str
    ) -> dict[str, Any]:
        return self._role_request(
            "revoke_worker",
            {
                "target_node_id": target_node_id,
                "cluster_id": cluster_id,
                "epoch": epoch,
                "fencing_token": fencing_token,
            },
        )

    def resume_worker(
        self, target_node_id: str, *, cluster_id: str, epoch: int, fencing_token: str
    ) -> dict[str, Any]:
        return self._role_request(
            "resume_worker",
            {
                "target_node_id": target_node_id,
                "cluster_id": cluster_id,
                "epoch": epoch,
                "fencing_token": fencing_token,
            },
        )

    def remove_connection(
        self, target_node_id: str, *, cluster_id: str, epoch: int, fencing_token: str
    ) -> dict[str, Any]:
        return self._role_request(
            "remove_connection",
            {
                "target_node_id": target_node_id,
                "cluster_id": cluster_id,
                "epoch": epoch,
                "fencing_token": fencing_token,
            },
        )

    def remove_job(
        self, target_node_id: str, *, cluster_id: str, epoch: int, fencing_token: str
    ) -> dict[str, Any]:
        return self._role_request(
            "remove_job",
            {
                "target_node_id": target_node_id,
                "cluster_id": cluster_id,
                "epoch": epoch,
                "fencing_token": fencing_token,
            },
        )

    def revoke_member(
        self, target_node_id: str, *, cluster_id: str, epoch: int, fencing_token: str
    ) -> dict[str, Any]:
        return self._role_request(
            "revoke_member",
            {
                "target_node_id": target_node_id,
                "cluster_id": cluster_id,
                "epoch": epoch,
                "fencing_token": fencing_token,
            },
        )

    def grant_capabilities(
        self,
        subject_node_id: str,
        target_node_id: str,
        permissions: list[str],
        *,
        expires_at: float,
        cluster_id: str,
        epoch: int,
        fencing_token: str,
    ) -> dict[str, Any]:
        return self._role_request(
            "grant_capabilities",
            {
                "subject_node_id": subject_node_id,
                "target_node_id": target_node_id,
                "permissions": permissions,
                "expires_at": expires_at,
                "cluster_id": cluster_id,
                "epoch": epoch,
                "fencing_token": fencing_token,
            },
        )

    def revoke_capabilities(
        self,
        subject_node_id: str,
        target_node_id: str,
        *,
        cluster_id: str,
        epoch: int,
        fencing_token: str,
    ) -> dict[str, Any]:
        return self._role_request(
            "revoke_capabilities",
            {
                "subject_node_id": subject_node_id,
                "target_node_id": target_node_id,
                "cluster_id": cluster_id,
                "epoch": epoch,
                "fencing_token": fencing_token,
            },
        )

    def sync_capability_grant(
        self,
        subject_node_id: str,
        target_node_id: str,
        permissions: list[str],
        *,
        expires_at: float,
        cluster_id: str,
        epoch: int,
        fencing_token: str,
    ) -> dict[str, Any]:
        return self._role_request(
            "sync_capability_grant",
            {
                "subject_node_id": subject_node_id,
                "target_node_id": target_node_id,
                "permissions": permissions,
                "expires_at": expires_at,
                "cluster_id": cluster_id,
                "epoch": epoch,
                "fencing_token": fencing_token,
            },
        )

    def _role_request(self, operation: str, params: dict[str, Any]) -> dict[str, Any]:
        raise NotImplementedError


def dispatch_role_request(runtime: Any, request: Any) -> dict[str, Any]:
    """Apply an authenticated role request using runtime persistence hooks."""
    cluster = runtime._cluster
    if cluster is None:
        raise RuntimeError("cluster participation is disabled")
    caller = request.caller_node_id
    if caller is None:
        raise PermissionError("cluster operation has no caller identity")
    local_id = (
        runtime._identity.node_id if runtime._identity is not None else cluster.local_id
    )
    if (
        runtime._pairing is not None
        and caller != local_id
        and not runtime._pairing.can_join_cluster(caller)
    ):
        raise PermissionError("cluster operation requires an authenticated Pair")
    cluster.authorize(
        cluster_id=request.params["cluster_id"],
        epoch=request.params["epoch"],
        fencing_token=request.params["fencing_token"],
    )
    if request.op == "consume_invite":
        previous = (
            dict(cluster.assignments),
            dict(cluster._online),
            dict(cluster._invites),
            dict(cluster._join_admissions),
            set(cluster._used_invites),
        )
        invite = cluster.consume_invite(
            request.params["token"], caller, allow_retry=True
        )
        if not runtime._save_persisted_state():
            (
                cluster.assignments,
                cluster._online,
                cluster._invites,
                cluster._join_admissions,
                cluster._used_invites,
            ) = previous
            raise RuntimeError("cluster invite was not durably persisted")
        return {
            "target_node_id": invite.target_id.value,
            "expires_at": invite.expires_at,
            "cluster_id": cluster.cluster_id,
            "coordinator_id": cluster.coordinator_id.value,
            "epoch": cluster.epoch.epoch,
            "fencing_token": cluster.epoch.fencing_token,
        }
    actor = cluster.assignments.get(caller)
    if actor is None:
        raise PermissionError("caller is not a cluster member")
    state = RoleState(
        tuple(cluster.assignments.values()),
        cluster.epoch,
        capability_grants=runtime._cluster_capability_grants,
    )
    if request.op == "renew_coordinator_lease":
        if caller != cluster.coordinator_id:
            raise PermissionError("only the Coordinator may perform this operation")
        old = cluster.epoch
        cluster.epoch = renew_lease(
            old,
            coordinator_id=caller,
            fencing_token=request.params["fencing_token"],
            now=cluster._clock(),
        )
        if runtime._cluster_store is not None and not runtime._save_persisted_state():
            cluster.epoch = old
            raise RuntimeError("coordinator lease was not durably persisted")
        if runtime._service is not None:
            runtime._service.update_cluster_fence(
                cluster_id=cluster.cluster_id,
                coordinator_epoch=cluster.epoch.epoch,
                fencing_token=cluster.epoch.fencing_token,
            )
        return {
            "ok": True,
            "cluster_id": cluster.cluster_id,
            "epoch": cluster.epoch.epoch,
            "coordinator_id": cluster.coordinator_id.value,
        }
    if request.op == "assign_role":
        target, roles = (
            NodeId(request.params["target_node_id"]),
            request.params["roles"],
        )
        if caller != cluster.coordinator_id or len(roles) != 1:
            raise PermissionError("only the Coordinator may perform this operation")
        old = dict(cluster.assignments)
        old_online = dict(cluster._online)
        old_grants = runtime._cluster_capability_grants
        old_cluster_grants = cluster.capability_grants
        updated, _ = state.assign(
            actor=actor,
            target=target,
            roles=frozenset({ClusterRole(roles[0])}),
            now=time.time(),
        )
        cluster.assignments = {
            item.node_id: item
            for item in updated.assignments
            if item.node_id is not None
        }
        runtime._cluster_capability_grants = updated.capability_grants
        cluster.capability_grants = updated.capability_grants
        if not runtime._save_persisted_state():
            cluster.assignments = old
            cluster._online = old_online
            runtime._cluster_capability_grants = old_grants
            cluster.capability_grants = old_cluster_grants
            raise RuntimeError("cluster role was not durably persisted")
        runtime._hydrate_registry_membership()
        runtime._refresh_live_grants()
        return {"ok": True, "node_id": target.value, "role": roles[0]}
    if request.op in {
        "revoke_member",
        "revoke_worker",
        "pause_worker",
        "resume_worker",
        "remove_job",
    }:
        target, old, old_grants, old_cluster_grants = (
            NodeId(request.params["target_node_id"]),
            dict(cluster.assignments),
            runtime._cluster_capability_grants,
            cluster.capability_grants,
        )
        transition = {
            "revoke_member": state.revoke,
            "revoke_worker": state.revoke,
            "pause_worker": state.pause,
            "resume_worker": state.resume,
            "remove_job": state.remove_job,
        }[request.op]
        updated = transition(actor=actor, target=target)
        cluster.assignments = {
            item.node_id: item
            for item in updated.assignments
            if item.node_id is not None
        }
        runtime._cluster_capability_grants = updated.capability_grants
        cluster.capability_grants = updated.capability_grants
        if not runtime._save_persisted_state():
            cluster.assignments = old
            runtime._cluster_capability_grants = old_grants
            cluster.capability_grants = old_cluster_grants
            raise RuntimeError("cluster role mutation was not durably persisted")
        if request.op in {"revoke_member", "revoke_worker"}:
            runtime._refresh_live_grants()
        return {
            "ok": True,
            "node_id": target.value,
            "revoked": request.op in {"revoke_member", "revoke_worker"},
        }
    if request.op == "remove_connection":
        target = NodeId(request.params["target_node_id"])
        if caller != cluster.coordinator_id and caller != target:
            raise PermissionError(
                "only the Coordinator or target Worker may remove a connection"
            )
        if runtime._connection_manager is not None:
            runtime._connection_manager.disconnect(
                target, reason="removed by cluster operation"
            )
        return {"ok": True, "node_id": target.value, "connected": False}
    if request.op in {
        "grant_capabilities",
        "revoke_capabilities",
        "sync_capability_grant",
    }:
        if caller != cluster.coordinator_id:
            raise PermissionError("only the Coordinator may perform this operation")
        subject, target = (
            NodeId(request.params["subject_node_id"]),
            NodeId(request.params["target_node_id"]),
        )
        old_grants = runtime._cluster_capability_grants
        old_cluster_grants = cluster.capability_grants
        if request.op == "revoke_capabilities":
            updated = state.revoke_capabilities(
                actor=actor, subject=subject, target=target
            )
        else:
            updated = state.grant_capabilities(
                actor=actor,
                subject=subject,
                target=target,
                permissions=frozenset(
                    NodePermission(item) for item in request.params["permissions"]
                ),
                now=time.time(),
                expires_at=float(request.params["expires_at"]),
            )
        runtime._cluster_capability_grants = updated.capability_grants
        cluster.capability_grants = updated.capability_grants
        if not runtime._save_persisted_state():
            runtime._cluster_capability_grants = old_grants
            cluster.capability_grants = old_cluster_grants
            raise RuntimeError("capability mutation was not durably persisted")
        runtime._refresh_live_grants()
        return {
            "ok": True,
            "subject_node_id": subject.value,
            "target_node_id": target.value,
        }
    if request.op in {"worker_snapshot", "standby_batch"}:
        return {"ok": False, "supported": False, "reason": "host_supplied"}
    raise ValueError(f"unsupported cluster operation: {request.op}")

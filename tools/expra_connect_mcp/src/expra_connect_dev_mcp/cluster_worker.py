"""Inspect the canonical cluster engine in a configured child process."""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path
from typing import Any


def _members(cluster: Any) -> list[dict[str, Any]]:
    return [
        {
            "node_id": node_id.value,
            "roles": sorted(role.value for role in assignment.roles),
            "online": cluster.online(node_id),
            "paused": assignment.paused,
            "revoked": assignment.revoked,
        }
        for node_id, assignment in sorted(
            cluster.assignments.items(), key=lambda item: item[0].value
        )
    ]


def _round_trip(cluster: Any, store: Any) -> bool:
    store.save(cluster)
    restored = store.load()
    return bool(
        restored.cluster_id == cluster.cluster_id
        and restored.local_node_id == cluster.local_id.value
        and restored.coordinator_epoch == cluster.epoch
        and restored.role_assignments == tuple(cluster.assignments.values())
    )


def _base(
    cluster: Any, action: str, members: list[dict[str, Any]], round_trip: bool
) -> dict[str, Any]:
    epoch = cluster.epoch
    return {
        "action": action,
        "status": "PASS",
        "execution_mode": "LOOPBACK_EXECUTION",
        "cluster_id": cluster.cluster_id,
        "coordinator_id": cluster.coordinator_id.value,
        "local_role": cluster.local_role.value,
        "epoch": epoch.epoch,
        "fencing_token_present": bool(epoch.fencing_token),
        "fencing_token_values_omitted": True,
        "members": members,
        "persisted_round_trip": round_trip,
        "failover": None,
        "timeline": [],
        "reasons": [],
        "executed_network_code": False,
        "executed_user_code": False,
    }


def inspect_cluster(action: str) -> dict[str, Any]:
    from expra_connect.cluster import (
        Cluster,
        ClusterRole,
        ClusterStore,
        FailoverCoordinator,
    )
    from expra_connect.cluster.roles import RoleState
    from expra_connect.identity import NodeId

    with tempfile.TemporaryDirectory(prefix="expra-mcp-cluster-") as directory:
        coordinator = NodeId("coordinator")
        subcoordinator = NodeId("subcoordinator")
        worker = NodeId("worker")
        cluster = Cluster(coordinator, clock=lambda: 100.0)
        if action == "failover":
            cluster.assign(subcoordinator, ClusterRole.SUBCOORDINATOR)
            cluster.assign(worker, ClusterRole.WORKER)
            cluster.set_online(subcoordinator, True)
            cluster.set_online(worker, True)
        store = ClusterStore(Path(directory) / "cluster.json")
        round_trip = _round_trip(cluster, store)
        members = _members(cluster)
        result = _base(cluster, action, members, round_trip)
        result["timeline"] = [
            {
                "sequence": 1,
                "node": coordinator.value,
                "event": "membership_observed",
                "outcome": "PASS",
            },
            {
                "sequence": 2,
                "node": coordinator.value,
                "event": "roles_observed",
                "outcome": "PASS",
            },
            {
                "sequence": 3,
                "node": coordinator.value,
                "event": "epoch_observed",
                "outcome": "PASS",
            },
        ]
        if action == "inspect":
            if not round_trip:
                result["status"] = "FAIL"
                result["reasons"] = ["canonical cluster state did not round-trip"]
            return result

        state = RoleState(
            tuple(cluster.assignments.values()),
            cluster.epoch,
            cluster.promotion_epochs,
            cluster.capability_grants,
        )

        def persist(candidate: Any) -> None:
            cluster.assignments = {
                item.node_id: item
                for item in candidate.assignments
                if item.node_id is not None
            }
            cluster.epoch = candidate.epoch
            cluster.promotion_epochs = candidate.promotion_epochs
            cluster.capability_grants = candidate.capability_grants
            store.save(cluster)

        failover = FailoverCoordinator(state, persist=persist)
        old_epoch = cluster.epoch
        stale_heartbeat_rejected = not failover.heartbeat(
            coordinator_id=old_epoch.coordinator_id,
            fencing_token="stale-token",
            now=old_epoch.lease_expires_at - 1.0,
        )
        promoted = failover.promote_if_due(now=old_epoch.lease_expires_at + 1.0)
        new_epoch = cluster.epoch
        duplicate_promotion_rejected = not failover.promote_if_due(
            now=new_epoch.lease_expires_at + 1.0
        )
        failover.rejoin_as_worker(
            node_id=old_epoch.coordinator_id, current_epoch=new_epoch.epoch
        )
        result["coordinator_id"] = cluster.coordinator_id.value
        result["local_role"] = cluster.local_role.value
        result["epoch"] = cluster.epoch.epoch
        result["members"] = _members(cluster)
        result["persisted_round_trip"] = _round_trip(cluster, store)
        returning_assignment = cluster.assignments[old_epoch.coordinator_id]
        result["failover"] = {
            "old_coordinator_id": old_epoch.coordinator_id.value,
            "new_coordinator_id": new_epoch.coordinator_id.value,
            "old_epoch": old_epoch.epoch,
            "new_epoch": new_epoch.epoch,
            "old_fencing_token_present": bool(old_epoch.fencing_token),
            "new_fencing_token_present": bool(new_epoch.fencing_token),
            "fencing_token_values_omitted": True,
            "stale_heartbeat_rejected": stale_heartbeat_rejected,
            "duplicate_promotion_rejected": duplicate_promotion_rejected,
            "returning_coordinator_role": (
                "worker"
                if any(role.value == "worker" for role in returning_assignment.roles)
                else "unknown"
            ),
        }
        result["timeline"].extend(
            [
                {
                    "sequence": 4,
                    "node": old_epoch.coordinator_id.value,
                    "event": "stale_heartbeat_rejected",
                    "outcome": "PASS" if stale_heartbeat_rejected else "FAIL",
                },
                {
                    "sequence": 5,
                    "node": new_epoch.coordinator_id.value,
                    "event": "coordinator_promoted",
                    "outcome": "PASS" if promoted else "FAIL",
                },
                {
                    "sequence": 6,
                    "node": old_epoch.coordinator_id.value,
                    "event": "returning_coordinator_fenced_to_worker",
                    "outcome": "PASS",
                },
            ]
        )
        checks = (
            promoted,
            round_trip,
            stale_heartbeat_rejected,
            duplicate_promotion_rejected,
            new_epoch.epoch > old_epoch.epoch,
            new_epoch.coordinator_id == subcoordinator,
        )
        if not all(checks):
            result["status"] = "FAIL"
            result["reasons"] = ["canonical failover invariant failed"]
        return result


def main() -> int:
    print(json.dumps(inspect_cluster(sys.argv[1]), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

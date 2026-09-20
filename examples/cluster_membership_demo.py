"""Exercise explicit coordinator/worker membership and fencing locally.

Pairing establishes an authenticated relationship. Cluster membership is a
separate, opt-in state transition and must be tested separately.
"""

from __future__ import annotations

import argparse
import json
import tempfile
from pathlib import Path

from expra_connect import Cluster, ClusterRole, JsonStateStore, NodeId, __version__


def public_state(cluster: Cluster) -> dict[str, object]:
    """Return cluster evidence without exposing the fencing-token value."""
    state = cluster.to_dict()
    state["fencing_token_present"] = bool(state.pop("fencing_token", ""))

    def redact(value: object) -> None:
        if isinstance(value, dict):
            value.pop("fencing_token", None)
            for nested in value.values():
                redact(nested)
        elif isinstance(value, list):
            for nested in value:
                redact(nested)

    redact(state)
    return state


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", type=Path, default=None)
    args = parser.parse_args()

    # Two identities model the coordinator and worker independently. In a
    # physical test these would be the already-paired Linux and Windows nodes.
    coordinator = Cluster(NodeId("coordinator-node"))
    worker = Cluster(NodeId("worker-node"))
    invite = coordinator.create_invite(worker.local_id)

    print(
        json.dumps(
            {
                "event": "coordinator_ready",
                "version": __version__,
                "cluster": public_state(coordinator),
            },
            sort_keys=True,
        )
    )

    # The worker validates target, cluster, epoch, and invite expiry before it
    # becomes a member. Consuming the invite also assigns the worker role.
    worker.join(
        invite,
        coordinator,
        pairing=lambda peer_id: peer_id == coordinator.local_id,
    )
    print(
        json.dumps(
            {
                "event": "worker_joined",
                "worker": public_state(worker),
                "coordinator": public_state(coordinator),
            },
            sort_keys=True,
        )
    )

    assert worker.local_role is ClusterRole.WORKER
    coordinator.authorize(
        cluster_id=coordinator.cluster_id,
        epoch=coordinator.epoch.epoch,
        fencing_token=coordinator.epoch.fencing_token,
    )

    # A stale epoch or fencing token must never authorize coordinator work.
    try:
        coordinator.authorize(
            cluster_id=coordinator.cluster_id,
            epoch=coordinator.epoch.epoch - 1,
            fencing_token="stale-token",
        )
    except PermissionError as error:
        print(json.dumps({"event": "stale_fence_rejected", "reason": str(error)}))
    else:
        raise RuntimeError("stale coordinator authority was accepted")

    # Optional persistence proves the membership survives a restart boundary.
    if args.profile:
        args.profile.mkdir(parents=True, exist_ok=True)
    temporary_directory = (
        tempfile.TemporaryDirectory(dir=args.profile)
        if args.profile
        else tempfile.TemporaryDirectory()
    )
    with temporary_directory as directory:
        path = Path(directory) / "cluster.json"
        coordinator.save(JsonStateStore(path, kind="cluster"))
        restored = Cluster.load(
            coordinator.local_id, JsonStateStore(path, kind="cluster")
        )
        print(json.dumps({"event": "restored", "cluster": public_state(restored)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

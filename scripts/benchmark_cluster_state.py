"""Benchmark cluster-state codecs with representative in-memory fixtures."""

from __future__ import annotations

import argparse
import json
import platform
import statistics
import sys
import time
import tracemalloc
from collections.abc import Callable
from typing import Any

import yaml

from expra_connect.cluster import (
    CapabilityGrant,
    ClusterRole,
    ClusterState,
    CoordinatorEpoch,
    RoleAssignment,
    RoleState,
    role_state_from_dict,
    role_state_to_dict,
)
from expra_connect.data.permissions import NodePermission
from expra_connect.identity import NodeId


def _fixture(size: int) -> tuple[RoleState, ClusterState]:
    coordinator_id = NodeId("coordinator")
    assignments = (
        RoleAssignment(frozenset({ClusterRole.COORDINATOR}), coordinator_id),
        *(
            RoleAssignment(frozenset({ClusterRole.WORKER}), NodeId(f"worker-{i}"))
            for i in range(size - 1)
        ),
    )
    grants = tuple(
        CapabilityGrant(
            coordinator_id,
            assignment.node_id,
            frozenset({NodePermission.READ_STATE}),
            1.0,
            3600.0,
        )
        for assignment in assignments[1:]
        if assignment.node_id is not None
    )
    epoch = CoordinatorEpoch(1, coordinator_id, "fence", 0.0, 3600.0)
    role_state = RoleState(assignments, epoch, capability_grants=grants)
    return role_state, ClusterState(
        local_node_id=coordinator_id.value,
        cluster_id="benchmark-cluster",
        role_assignments=assignments,
        coordinator_epoch=epoch,
        capability_grants=grants,
    )


def _measure(
    operation: Callable[[], Any], *, warmups: int, repetitions: int
) -> tuple[float, float, float, int]:
    for _ in range(warmups):
        operation()
    samples: list[float] = []
    for _ in range(repetitions):
        started = time.perf_counter()
        operation()
        samples.append((time.perf_counter() - started) * 1_000_000)

    tracemalloc.start()
    try:
        tracemalloc.reset_peak()
        operation()
        _, peak_bytes = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    return (
        statistics.median(samples),
        min(samples),
        max(samples),
        peak_bytes,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--warmups", type=int, default=1)
    parser.add_argument("--repetitions", type=int, default=3)
    parser.add_argument(
        "--sizes", type=int, nargs="+", default=[1_000, 30_000, 100_000]
    )
    parser.add_argument("--yaml-max-size", type=int, default=30_000)
    args = parser.parse_args()
    if (
        args.warmups < 0
        or args.repetitions < 1
        or args.yaml_max_size < 0
        or any(size < 1 for size in args.sizes)
    ):
        parser.error("warmups and YAML max must be >= 0; repetitions and sizes >= 1")

    print(f"Python: {sys.version.split()[0]} ({platform.platform()})")
    print(
        f"Warmups: {args.warmups}; repetitions: {args.repetitions}; "
        f"YAML measured through {args.yaml_max_size:,} entries"
    )
    for size in args.sizes:
        role_state, cluster_state = _fixture(size)
        role_document = role_state_to_dict(role_state)
        cluster_document = cluster_state.to_dict()
        payload_bytes = len(json.dumps(cluster_document).encode("utf-8"))
        yaml_document: str | None = None
        yaml_payload_bytes = 0
        if size <= args.yaml_max_size:
            yaml_document = yaml.safe_dump(
                cluster_document, sort_keys=True, allow_unicode=True
            )
            yaml_payload_bytes = len(yaml_document.encode("utf-8"))
        operations: list[tuple[str, Callable[[], Any]]] = [
            (
                "role encode",
                lambda state=role_state: role_state_to_dict(state),
            ),
            (
                "role decode",
                lambda document=role_document: role_state_from_dict(document),
            ),
            ("cluster encode", cluster_state.to_dict),
            (
                "cluster decode",
                lambda document=cluster_document: ClusterState.from_dict(document),
            ),
            (
                "cluster round-trip",
                lambda state=cluster_state: ClusterState.from_dict(state.to_dict()),
            ),
        ]
        if yaml_document is not None:
            yaml_text = yaml_document
            operations.extend(
                (
                    (
                        "YAML encode",
                        lambda document=cluster_document: yaml.safe_dump(
                            document, sort_keys=True, allow_unicode=True
                        ),
                    ),
                    (
                        "YAML parse",
                        lambda document=yaml_text: yaml.safe_load(document),
                    ),
                    (
                        "YAML cluster round-trip",
                        lambda state=cluster_state: ClusterState.from_dict(
                            yaml.safe_load(
                                yaml.safe_dump(
                                    state.to_dict(),
                                    sort_keys=True,
                                    allow_unicode=True,
                                )
                            )
                        ),
                    ),
                )
            )
        yaml_size = (
            f"{yaml_payload_bytes:,} bytes"
            if yaml_document is not None
            else "not measured"
        )
        print(
            f"\nEntries: {size}; JSON payload: {payload_bytes:,} bytes; YAML payload: {yaml_size}"
        )
        for label, operation in operations:
            median, low, high, peak = _measure(
                operation, warmups=args.warmups, repetitions=args.repetitions
            )
            print(
                f"{label}: median={median:,.1f} us "
                f"range={low:,.1f}..{high:,.1f} us peak={peak:,} bytes"
            )


if __name__ == "__main__":
    main()

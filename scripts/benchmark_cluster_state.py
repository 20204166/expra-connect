"""Benchmark cluster-state codecs with representative in-memory fixtures."""

from __future__ import annotations

import argparse
import importlib
import json
import platform
import statistics
import sys
import time
import tracemalloc
from collections.abc import Callable
from functools import partial
from typing import Any, cast

import yaml

from expra_connect import __version__
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


def _records(
    size: int,
) -> tuple[tuple[RoleAssignment, ...], tuple[CapabilityGrant, ...], CoordinatorEpoch]:
    node_ids = tuple(NodeId(f"node-{index}") for index in range(size))
    assignments = tuple(
        RoleAssignment(
            frozenset(
                {ClusterRole.COORDINATOR, ClusterRole.WORKER}
                if index == 0
                else {ClusterRole.WORKER}
            ),
            node_id,
        )
        for index, node_id in enumerate(node_ids)
    )
    grants = tuple(
        CapabilityGrant(
            node_id,
            NodeId(f"target-{index}"),
            frozenset({NodePermission.READ_STATE}),
            100.0,
            200.0,
        )
        for index, node_id in enumerate(node_ids)
    )
    epoch = CoordinatorEpoch(23, node_ids[0], "benchmark-token", 100.0, 220.0)
    return assignments, grants, epoch


def _cluster_document(state: RoleState) -> dict[str, Any]:
    document = role_state_to_dict(state)
    document.update(
        {
            "schema_version": 2,
            "cluster_id": "benchmark-cluster",
            "local_node_id": (
                "node-0" if state.epoch is None else state.epoch.coordinator_id.value
            ),
            "active_invites": [],
            "join_admissions": [],
            "used_invites": [],
        }
    )
    return document


def _cluster_document_round_trip(document: dict[str, Any]) -> dict[str, Any]:
    return ClusterState.from_dict(document).to_dict()


def _cluster_state_round_trip(state: ClusterState) -> ClusterState:
    return ClusterState.from_dict(state.to_dict())


def _yaml_encode(document: dict[str, Any]) -> str:
    return cast(str, yaml.safe_dump(document, sort_keys=True, allow_unicode=True))


def _yaml_cluster_round_trip(state: ClusterState) -> ClusterState:
    encoded = yaml.safe_dump(state.to_dict(), sort_keys=True, allow_unicode=True)
    return ClusterState.from_dict(yaml.safe_load(encoded))


def _select_operations(
    operations: list[tuple[str, Callable[[], Any]]], selected: str | None
) -> list[tuple[str, Callable[[], Any]]]:
    if selected is None:
        return operations
    for name, operation in operations:
        if name == selected:
            return [(name, operation)]
    raise ValueError(f"unknown benchmark operation: {selected}")


def _measure(
    operation: Callable[[], Any],
    *,
    warmups: int,
    repetitions: int,
    batch_count: int = 1,
    track_allocations: bool = True,
) -> tuple[float, float, float, int]:
    if batch_count < 1:
        raise ValueError("batch_count must be positive")
    for _ in range(warmups):
        for _ in range(batch_count):
            operation()
    samples: list[float] = []
    for _ in range(repetitions):
        started = time.perf_counter_ns()
        for _ in range(batch_count):
            operation()
        elapsed_ns = time.perf_counter_ns() - started
        samples.append(elapsed_ns / batch_count / 1_000)

    if not track_allocations:
        return statistics.median(samples), min(samples), max(samples), -1

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
    parser.add_argument("--batch-count", type=int, default=1)
    parser.add_argument(
        "--sizes", type=int, nargs="+", default=[1_000, 30_000, 100_000]
    )
    parser.add_argument(
        "--skip-allocation-tracking",
        action="store_true",
        help="skip tracemalloc during large stress runs",
    )
    parser.add_argument(
        "--operation",
        choices=(
            "role encode",
            "role decode",
            "cluster encode",
            "cluster decode",
            "cluster round-trip",
            "cluster document round-trip",
            "YAML encode",
            "YAML parse",
            "YAML cluster round-trip",
        ),
    )
    parser.add_argument("--yaml-max-size", type=int, default=30_000)
    args = parser.parse_args()
    if (
        args.warmups < 0
        or args.repetitions < 1
        or args.batch_count < 1
        or args.yaml_max_size < 0
        or any(size < 1 for size in args.sizes)
    ):
        parser.error("warmups and YAML max must be >= 0; repetitions and sizes >= 1")
    if (
        args.operation is not None
        and args.operation.startswith("YAML ")
        and any(size > args.yaml_max_size for size in args.sizes)
    ):
        parser.error("selected YAML operation exceeds --yaml-max-size")

    print(
        f"version={__version__} python={sys.version.split()[0]} "
        f"platform={platform.platform()} operation={args.operation or 'all'}"
    )
    print(
        "fixture=staged_role_cluster "
        f"warmups={args.warmups} repetitions={args.repetitions} "
        f"batch_count={args.batch_count} yaml_max_size={args.yaml_max_size:,}"
    )
    for module_name in (
        "expra_connect.cluster.models",
        "expra_connect.cluster.operations",
        "expra_connect.cluster.roles",
        "expra_connect.cluster.state",
    ):
        module = importlib.import_module(module_name)
        print(f"module={module_name} path={module.__file__}")
    for size in args.sizes:
        assignments, grants, epoch = _records(size)
        needs_role_state = (
            args.operation is None
            or args.operation.startswith("role ")
            or args.operation
            in (
                "cluster decode",
                "cluster document round-trip",
                "YAML encode",
                "YAML parse",
                "YAML cluster round-trip",
            )
        )
        needs_cluster_state = args.operation is None or args.operation in (
            "cluster encode",
            "cluster round-trip",
            "YAML encode",
            "YAML parse",
            "YAML cluster round-trip",
        )
        role_state = (
            RoleState(assignments, epoch, capability_grants=grants)
            if needs_role_state
            else None
        )
        cluster_state = (
            ClusterState(
                local_node_id="node-0",
                cluster_id="benchmark-cluster",
                role_assignments=assignments,
                coordinator_epoch=epoch,
                capability_grants=grants,
            )
            if needs_cluster_state
            else None
        )
        role_document = (
            role_state_to_dict(role_state)
            if role_state is not None and args.operation in (None, "role decode")
            else None
        )
        cluster_document = (
            _cluster_document(role_state)
            if role_state is not None
            and args.operation
            in (
                None,
                "cluster decode",
                "cluster document round-trip",
                "YAML encode",
                "YAML parse",
                "YAML cluster round-trip",
            )
            else None
        )
        payload_document = (
            role_document
            if args.operation is not None and args.operation.startswith("role ")
            else cluster_document
        )
        payload_bytes = (
            0
            if payload_document is None
            else len(json.dumps(payload_document).encode("utf-8"))
        )
        yaml_document: str | None = None
        yaml_payload_bytes = 0
        if cluster_document is not None and size <= args.yaml_max_size:
            yaml_document = yaml.safe_dump(
                cluster_document, sort_keys=True, allow_unicode=True
            )
            yaml_payload_bytes = len(yaml_document.encode("utf-8"))
        operations: list[tuple[str, Callable[[], Any]]] = []
        if role_state is not None:
            operations.append(("role encode", partial(role_state_to_dict, role_state)))
        if role_document is not None:
            operations.append(
                (
                    "role decode",
                    partial(role_state_from_dict, role_document),
                )
            )
        if cluster_state is not None:
            operations.append(("cluster encode", cluster_state.to_dict))
        if cluster_document is not None:
            operations.append(
                (
                    "cluster decode",
                    partial(ClusterState.from_dict, cluster_document),
                )
            )
            operations.append(
                (
                    "cluster document round-trip",
                    partial(_cluster_document_round_trip, cluster_document),
                )
            )
        if cluster_state is not None:
            operations.append(
                (
                    "cluster round-trip",
                    partial(_cluster_state_round_trip, cluster_state),
                )
            )
        if yaml_document is not None:
            yaml_text = yaml_document
            assert cluster_document is not None
            assert cluster_state is not None
            operations.extend(
                (
                    (
                        "YAML encode",
                        partial(_yaml_encode, cluster_document),
                    ),
                    (
                        "YAML parse",
                        partial(yaml.safe_load, yaml_text),
                    ),
                    (
                        "YAML cluster round-trip",
                        partial(_yaml_cluster_round_trip, cluster_state),
                    ),
                )
            )
        yaml_size = (
            f"{yaml_payload_bytes:,} bytes"
            if yaml_document is not None
            else "not measured"
        )
        print(
            f"\nEntries: {size}; assignments: {len(assignments):,}; "
            f"grants: {len(grants):,}; JSON payload: {payload_bytes:,} bytes; "
            f"YAML payload: {yaml_size}"
        )
        selected_operations = _select_operations(operations, args.operation)
        for label, operation in selected_operations:
            median, low, high, peak = _measure(
                operation,
                warmups=args.warmups,
                repetitions=args.repetitions,
                batch_count=args.batch_count,
                track_allocations=not args.skip_allocation_tracking,
            )
            peak_label = f"{peak:,} bytes" if peak >= 0 else "not measured"
            print(
                f"{label}: median={median:,.1f} us "
                f"range={low:,.1f}..{high:,.1f} us peak={peak_label}"
            )


if __name__ == "__main__":
    main()

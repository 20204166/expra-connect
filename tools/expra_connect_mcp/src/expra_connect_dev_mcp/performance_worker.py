"""Measure real loopback runtime paths and bounded resource retention."""

from __future__ import annotations

import json
import tempfile
import time
from pathlib import Path
from typing import Any

from .scenario_worker import NodeProcess


def _local_discovery_samples(count: int = 4) -> list[float]:
    from .runtime_worker import _local_discovery

    samples: list[float] = []
    for _ in range(count):
        started = time.perf_counter()
        result = _local_discovery()
        samples.append(time.perf_counter() - started)
        if result.get("status") != "PASS":
            raise RuntimeError("discovery probe did not pass")
    return samples


def _connection_samples(
    root: Path,
) -> tuple[
    list[float],
    list[float],
    dict[str, Any],
    dict[str, Any],
    dict[str, Any],
    bool,
]:
    node_a = NodeProcess(root / "node-a", "node-a")
    node_b = NodeProcess(root / "node-b", "node-b")
    try:
        node_a.request("start")
        node_b_start = node_b.request("start")
        node_a.request("inject_candidate", node_b_start["advertisement"])
        peer_id = node_b_start["advertisement"]["node_id"]
        node_a.request("pair", {"peer_id": peer_id})
        node_a.request("connect", {"peer_id": peer_id})
        before = node_a.request("resources")["resources"]

        connect_samples: list[float] = []
        for _ in range(3):
            node_a.request("disconnect", {"peer_id": peer_id})
            started = time.perf_counter()
            node_a.request("connect", {"peer_id": peer_id})
            connect_samples.append(time.perf_counter() - started)

        reconnect_samples: list[float] = []
        for _ in range(3):
            started = time.perf_counter()
            node_a.request("reconnect", {"peer_id": peer_id})
            reconnect_samples.append(time.perf_counter() - started)
        after = node_a.request("resources")["resources"]
        after_state = node_a.request("state")["state"]
        result = (connect_samples, reconnect_samples, before, after, after_state)
    finally:
        node_a.close()
        node_b.close()
    reaped = node_a.process.poll() is not None and node_b.process.poll() is not None
    return (*result, reaped)


def _retry_evidence() -> dict[str, Any]:
    from expra_connect import RetryState
    from expra_connect.connection_state import PeerFailure

    retry = RetryState()
    attempts: list[int] = []
    delays: list[float] = []
    for now in (100.0, 101.0, 103.0):
        retry.record_failure(PeerFailure.CONNECTION_REFUSED, now=now)
        attempts.append(retry.attempt)
        if retry.next_attempt_at is None:
            raise RuntimeError("retry cadence did not schedule an attempt")
        delays.append(retry.next_attempt_at - now)
    retry.record_failure(PeerFailure.AUTHENTICATION_FAILED, now=107.0)
    return {
        "attempts": attempts,
        "delays_seconds": delays,
        "authentication_disables_retry": (
            not retry.automatic_retry and retry.next_attempt_at is None
        ),
        "cadence_source": "expra_connect.connection_state.RetryState",
    }


def _latency(name: str, samples: list[float], warmup_count: int) -> dict[str, Any]:
    from expra_connect.observability import summarize_samples

    return {
        "name": name,
        "sample_count": len(samples),
        "warmup_count": warmup_count,
        "distribution": summarize_samples(tuple(samples)),
        "measurement_scope": "isolated child runtime path",
    }


def execute(action: str) -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="expra-mcp-performance-") as directory:
        connect, reconnect, before, after, after_state, workers_started = (
            _connection_samples(Path(directory))
        )
        discovery = _local_discovery_samples()
        retry = _retry_evidence()
        retention = {
            "threads_before": before["thread_count"],
            "threads_after": after["thread_count"],
            "tasks_before": before["task_count"],
            "tasks_after": after["task_count"],
            "sockets_before": before["socket_count"],
            "sockets_after": after["socket_count"],
            "registry_before": before["registry_count"],
            "registry_after": after["registry_count"],
            "worker_processes_reaped": workers_started,
            "bounded": (
                after["thread_count"] <= before["thread_count"]
                and after["task_count"] <= before["task_count"]
                and after["socket_count"] <= before["socket_count"]
                and after["registry_count"] == before["registry_count"]
            ),
        }
        status = "PASS" if retention["bounded"] else "FAIL"
        return {
            "action": action,
            "status": status,
            "execution_mode": "LOOPBACK_EXECUTION",
            "latencies": [
                _latency("discovery", discovery[1:], 1),
                _latency("connect", connect, 1),
                _latency("reconnect", reconnect, 1),
            ],
            "retry": retry,
            "retention": retention,
            "diagnostics": {
                "connected_peer_count": after_state["connection_count"],
                "registry_record_count": after["registry_count"],
                "latency_sample_count": len(discovery[1:])
                + len(connect)
                + len(reconnect),
            },
            "reasons": [] if status == "PASS" else ["resource counts were retained"],
            "executed_network_code": True,
            "executed_user_code": False,
        }


def main() -> int:
    import sys

    print(json.dumps(execute(sys.argv[1]), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

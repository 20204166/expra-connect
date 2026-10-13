"""Reproducible performance harness for Expra Connect.

Run with the installed (editable) package:

    .venv/bin/python tools/connect_performance_benchmark.py
    .venv-pypy/bin/python tools/connect_performance_benchmark.py [if PyPy venv exists]

Workloads:
    A. Local CPU — identity fingerprint, canonical JSON, sign/verify envelope,
       HMAC signature, session-registry operations.
    B. Persistence — YAML and msgpack state-store round-trips.
    C. Replay cache — bounded LRU check-and-record under load.
    D. Identity lifecycle — create, save, load.

Each workload runs WARMUP iterations (discarded) then MEASURE iterations.
Statistics: min, p50, p95, mean (all in microseconds), and throughput (ops/s).
"""

from __future__ import annotations

import math
import platform
import secrets
import statistics
import sys
import tempfile
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Imports from the canonical library (no algorithm duplication)
# ---------------------------------------------------------------------------
from expra_connect.identity import NodeId, NodeIdentity, node_identity_fingerprint
from expra_connect.persistence import MessagePackStateStore, YamlStateStore
from expra_connect.session import LogicalSessionRegistry
from expra_connect.wire_protocol import (
    RemoteRequest,
    ReplayCache,
    sign_request,
    sign_response,
    verify_request,
    verify_response,
)

# ---------------------------------------------------------------------------
# Harness parameters
# ---------------------------------------------------------------------------
WARMUP = 500
MEASURE = 5_000

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _percentile(sorted_data: list[float], pct: float) -> float:
    if not sorted_data:
        return float("nan")
    idx = (len(sorted_data) - 1) * pct / 100
    lo, hi = int(idx), min(int(idx) + 1, len(sorted_data) - 1)
    return sorted_data[lo] + (sorted_data[hi] - sorted_data[lo]) * (idx - lo)


def bench(
    label: str,
    fn: Callable[[], Any],
    *,
    warmup: int = WARMUP,
    n: int = MEASURE,
) -> dict[str, float]:
    for _ in range(warmup):
        fn()
    samples: list[float] = []
    for _ in range(n):
        t0 = time.perf_counter()
        fn()
        samples.append((time.perf_counter() - t0) * 1_000_000)
    samples.sort()
    mean = statistics.mean(samples)
    result = {
        "min_us": samples[0],
        "p50_us": _percentile(samples, 50),
        "p95_us": _percentile(samples, 95),
        "mean_us": mean,
        "ops_per_s": 1_000_000 / mean if mean > 0 else math.inf,
    }
    print(
        f"  {label:<46}  "
        f"p50={result['p50_us']:7.2f}µs  "
        f"p95={result['p95_us']:7.2f}µs  "
        f"ops/s={result['ops_per_s']:>10,.0f}"
    )
    return result


# ---------------------------------------------------------------------------
# Fixture setup
# ---------------------------------------------------------------------------

SECRET = secrets.token_hex(32)
NODE_A = NodeId(secrets.token_hex(16))
NODE_B = NodeId(secrets.token_hex(16))
_IDENTITY = NodeIdentity.create(NODE_A)  # kept to pre-generate keys once

_counter = 0


def _fresh_ids() -> tuple[str, str]:
    global _counter
    _counter += 1
    return secrets.token_hex(8), secrets.token_hex(8)


# ---------------------------------------------------------------------------
# Workload A: Local CPU
# ---------------------------------------------------------------------------


def _bench_fingerprint() -> None:
    node_identity_fingerprint(NODE_A)


def _bench_sign_request() -> None:
    rid, nonce = _fresh_ids()
    sign_request(
        node_id=NODE_A.value,
        op="ping",
        params={},
        request_id=rid,
        nonce=nonce,
        timestamp=time.time(),
        secret=SECRET,
    )


def _bench_sign_response() -> None:
    sign_response(
        node_id=NODE_A.value,
        request_id=secrets.token_hex(8),
        status="ok",
        payload={"ready": True},
        timestamp=time.time(),
        secret=SECRET,
    )


_REPLAY_CACHE = ReplayCache(clock=time.monotonic, max_entries=WARMUP + MEASURE + 100)


def _bench_verify_request() -> None:
    rid, nonce = _fresh_ids()
    env = sign_request(
        node_id=NODE_A.value,
        op="ping",
        params={},
        request_id=rid,
        nonce=nonce,
        timestamp=time.time(),
        secret=SECRET,
    )
    verify_request(
        env,
        secret=SECRET,
        clock=time.time,
        freshness_seconds=60.0,
        replay_cache=_REPLAY_CACHE,
    )


def _bench_verify_response() -> None:
    env = sign_response(
        node_id=NODE_B.value,
        request_id=secrets.token_hex(8),
        status="ok",
        payload={"ok": True},
        timestamp=time.time(),
        secret=SECRET,
    )
    verify_response(env, secret=SECRET, clock=time.time, freshness_seconds=60.0)


# ---------------------------------------------------------------------------
# Workload B: Persistence
# ---------------------------------------------------------------------------

_YAML_PAYLOAD: dict[str, Any] = {
    "version": 2,
    "peers": [
        {"id": secrets.token_hex(8), "secret": secrets.token_hex(32)} for _ in range(5)
    ],
}
_MSGPACK_PAYLOAD = _YAML_PAYLOAD.copy()


def _bench_yaml_save(store: YamlStateStore) -> Callable[[], None]:
    def _run() -> None:
        store.save(_YAML_PAYLOAD)

    return _run


def _bench_yaml_load(store: YamlStateStore) -> Callable[[], None]:
    def _run() -> None:
        store.load()

    return _run


def _bench_msgpack_save(store: MessagePackStateStore) -> Callable[[], None]:
    def _run() -> None:
        store.save(_MSGPACK_PAYLOAD)

    return _run


def _bench_msgpack_load(store: MessagePackStateStore) -> Callable[[], None]:
    def _run() -> None:
        store.load()

    return _run


# ---------------------------------------------------------------------------
# Workload C: Replay cache — bounded check-and-record
# ---------------------------------------------------------------------------

_SMALL_CACHE = ReplayCache(clock=time.monotonic, max_entries=256)
_SMALL_CACHE_COUNTER = 0


def _bench_replay_cache() -> None:
    global _SMALL_CACHE_COUNTER
    _SMALL_CACHE_COUNTER += 1
    _SMALL_CACHE.check_and_record(
        NODE_A.value,
        f"req-{_SMALL_CACHE_COUNTER}",
        f"nonce-{_SMALL_CACHE_COUNTER}",
        time.monotonic(),
    )


# ---------------------------------------------------------------------------
# Workload D: Session registry
# ---------------------------------------------------------------------------

_SESSION_REG = LogicalSessionRegistry(
    clock=time.monotonic, max_sessions=WARMUP + MEASURE + 100
)
_GEN_COUNTER = 0


def _make_anon_request(rid: str, nonce: str) -> RemoteRequest:
    return RemoteRequest(
        node_id=NODE_A,
        caller_node_id=None,
        op="ping",
        params={},
        request_id=rid,
        nonce=nonce,
        timestamp=time.time(),
        session_id=None,
        resume=False,
        connection_generation=None,
    )


def _bench_session_open_assert() -> None:
    global _GEN_COUNTER
    _GEN_COUNTER += 1
    gen = f"gen-{_GEN_COUNTER}"
    rid, nonce = _fresh_ids()
    req = _make_anon_request(rid, nonce)
    sid = _SESSION_REG.authenticate(req, connection_generation=gen)
    _SESSION_REG.assert_current(sid, gen)


# ---------------------------------------------------------------------------
# Workload E: Identity lifecycle (slower — fewer iterations)
# ---------------------------------------------------------------------------


def _bench_identity_create() -> None:
    NodeIdentity.create()


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> None:
    print()
    print("=" * 75)
    print("  Expra Connect — Performance Benchmark")
    print(f"  Python {sys.version}")
    print(f"  Platform: {platform.platform()}")
    print("=" * 75)
    print()

    print("A. Local CPU — signing and verification")
    bench("identity fingerprint", _bench_fingerprint)
    bench("sign_request (ping)", _bench_sign_request)
    bench("sign_response", _bench_sign_response)
    bench("verify_response", _bench_verify_response)
    bench("verify_request (full: sign+verify+replay)", _bench_verify_request)
    print()

    print("B. Persistence — YAML and msgpack round-trips")
    with tempfile.TemporaryDirectory() as tmpdir:
        yaml_store = YamlStateStore(Path(tmpdir) / "state.yaml")
        yaml_store.save(_YAML_PAYLOAD)  # pre-create
        msgpack_store = MessagePackStateStore(Path(tmpdir) / "state.msgpack")
        msgpack_store.save(_MSGPACK_PAYLOAD)  # pre-create

        bench("yaml save (5-peer payload)", _bench_yaml_save(yaml_store))
        bench("yaml load", _bench_yaml_load(yaml_store))
        bench("msgpack save (5-peer payload)", _bench_msgpack_save(msgpack_store))
        bench("msgpack load", _bench_msgpack_load(msgpack_store))
    print()

    print("C. Replay cache — check-and-record")
    bench("replay cache check_and_record", _bench_replay_cache)
    print()

    print("D. Session registry")
    bench("open_or_resume + assert_current", _bench_session_open_assert)
    print()

    print("E. Identity lifecycle (n=200, warmup=10)")
    bench(
        "NodeIdentity.create() [Ed25519 keygen]",
        _bench_identity_create,
        warmup=10,
        n=200,
    )
    print()

    print("=" * 75)
    print("Done.")
    print()


if __name__ == "__main__":
    main()

"""Reproducible performance harness for Expra Connect.

Run with the installed (editable) package:

    .venv/bin/python -m tools.connect_performance_benchmark
    .venv-pypy/bin/python -m tools.connect_performance_benchmark [if PyPy venv exists]

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

import argparse
import platform
import secrets
import sys
import tempfile
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Imports from the canonical library (no algorithm duplication)
# ---------------------------------------------------------------------------
from expra_connect.identity import NodeId, NodeIdentity, node_identity_fingerprint
from expra_connect.persistence import MessagePackStateStore, YamlStateStore
from expra_connect.remote.session import LogicalSessionRegistry
from expra_connect.remote.wire_protocol import (
    RemoteRequest,
    ReplayCache,
    sign_request,
    sign_response,
    verify_request,
    verify_response,
)
from scripts.benchmark_support import (
    BenchmarkOutcome,
    MeasurementConfig,
    RunMetadata,
    collect_modules,
    format_result,
    measure,
    write_results,
)

# ---------------------------------------------------------------------------
# Harness parameters
# ---------------------------------------------------------------------------
WARMUP = 500
MEASURE = 5_000

# ---------------------------------------------------------------------------
# Workload descriptors
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class OperationSpec:
    name: str
    operation: Callable[[], Any]
    fixture: dict[str, Any]
    size: int
    stateful: bool = False
    payload_bytes: int | None = None
    warmups: int = WARMUP
    repetitions: int = MEASURE


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
    parser = argparse.ArgumentParser(description=__doc__)
    operation_names = (
        "identity fingerprint",
        "sign_request (ping)",
        "sign_response",
        "verify_response",
        "verify_request (full: sign+verify+replay)",
        "yaml save (5-peer payload)",
        "yaml load",
        "msgpack save (5-peer payload)",
        "msgpack load",
        "replay cache check_and_record",
        "open_or_resume + assert_current",
        "NodeIdentity.create() [Ed25519 keygen]",
    )
    parser.add_argument("--operation", choices=operation_names)
    parser.add_argument("--warmups", type=int)
    parser.add_argument("--repetitions", type=int)
    parser.add_argument("--batch-count", type=int, default=1)
    parser.add_argument("--track-allocations", action="store_true")
    parser.add_argument("--json-output", type=Path)
    args = parser.parse_args()
    try:
        base_config = MeasurementConfig(
            warmups=WARMUP if args.warmups is None else args.warmups,
            repetitions=MEASURE if args.repetitions is None else args.repetitions,
            batch_count=args.batch_count,
            track_allocations=args.track_allocations,
        )
    except (TypeError, ValueError) as error:
        parser.error(str(error))

    stateful_operations = {
        "verify_request (full: sign+verify+replay)",
        "replay cache check_and_record",
        "open_or_resume + assert_current",
    }
    selected_names = (
        {args.operation} if args.operation is not None else set(operation_names)
    )
    if args.batch_count > 1 and selected_names & stateful_operations:
        parser.error("stateful operation cannot be batched")

    from expra_connect import __version__

    modules = collect_modules(
        (
            "expra_connect.identity",
            "expra_connect.persistence",
            "expra_connect.remote.session",
            "expra_connect.remote.wire_protocol",
        )
    )
    print(
        f"version={__version__} python={sys.version.split()[0]} "
        f"platform={platform.platform()} operation={args.operation or 'all'}"
    )
    for module_name, module_path in modules.items():
        print(f"module={module_name} path={module_path}")

    outcomes: list[BenchmarkOutcome] = []
    with tempfile.TemporaryDirectory() as tmpdir:
        yaml_path = Path(tmpdir) / "state.yaml"
        msgpack_path = Path(tmpdir) / "state.msgpack"
        yaml_store = YamlStateStore(yaml_path)
        yaml_store.save(_YAML_PAYLOAD)
        msgpack_store = MessagePackStateStore(msgpack_path)
        msgpack_store.save(_MSGPACK_PAYLOAD)
        state_fixture = {"shape": "five_peer_state", "peers": 5}
        operations = (
            OperationSpec(
                "identity fingerprint", _bench_fingerprint, {"shape": "node_id"}, 1
            ),
            OperationSpec(
                "sign_request (ping)", _bench_sign_request, {"shape": "ping_request"}, 1
            ),
            OperationSpec(
                "sign_response", _bench_sign_response, {"shape": "small_response"}, 1
            ),
            OperationSpec(
                "verify_response",
                _bench_verify_response,
                {"shape": "small_response"},
                1,
            ),
            OperationSpec(
                "verify_request (full: sign+verify+replay)",
                _bench_verify_request,
                {"shape": "ping_request"},
                1,
                stateful=True,
            ),
            OperationSpec(
                "yaml save (5-peer payload)",
                _bench_yaml_save(yaml_store),
                state_fixture,
                5,
                payload_bytes=yaml_path.stat().st_size,
            ),
            OperationSpec(
                "yaml load",
                _bench_yaml_load(yaml_store),
                state_fixture,
                5,
                payload_bytes=yaml_path.stat().st_size,
            ),
            OperationSpec(
                "msgpack save (5-peer payload)",
                _bench_msgpack_save(msgpack_store),
                state_fixture,
                5,
                payload_bytes=msgpack_path.stat().st_size,
            ),
            OperationSpec(
                "msgpack load",
                _bench_msgpack_load(msgpack_store),
                state_fixture,
                5,
                payload_bytes=msgpack_path.stat().st_size,
            ),
            OperationSpec(
                "replay cache check_and_record",
                _bench_replay_cache,
                {"shape": "unique_replay_record"},
                1,
                stateful=True,
            ),
            OperationSpec(
                "open_or_resume + assert_current",
                _bench_session_open_assert,
                {"shape": "anonymous_session"},
                1,
                stateful=True,
            ),
            OperationSpec(
                "NodeIdentity.create() [Ed25519 keygen]",
                _bench_identity_create,
                {"shape": "generated_ed25519_identity"},
                1,
                warmups=10,
                repetitions=200,
            ),
        )
        for spec in operations:
            if args.operation is not None and spec.name != args.operation:
                continue
            config = MeasurementConfig(
                warmups=(spec.warmups if args.warmups is None else args.warmups),
                repetitions=(
                    spec.repetitions if args.repetitions is None else args.repetitions
                ),
                batch_count=args.batch_count,
                track_allocations=args.track_allocations,
            )
            result = measure(spec.operation, config)
            outcomes.append(
                BenchmarkOutcome(
                    case_id=spec.name,
                    operation=spec.name,
                    fixture=spec.fixture,
                    size=spec.size,
                    payload_bytes=spec.payload_bytes,
                    config=config,
                    result=result,
                )
            )
            print(f"{spec.name}: {format_result(result)}")

    if args.json_output is not None:
        write_results(
            args.json_output,
            RunMetadata(
                schema_version=1,
                suite="connect",
                package_version=__version__,
                python_version=sys.version.split()[0],
                platform=platform.platform(),
                modules=modules,
                config=base_config,
            ),
            outcomes,
        )


if __name__ == "__main__":
    main()

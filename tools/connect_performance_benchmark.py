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
import importlib
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
SESSION_STRESS_ACTIVE_COUNT = 900
SESSION_STRESS_MAX_SESSIONS = 1_024


def _import_version_module(primary: str, legacy: str) -> Any:
    try:
        return importlib.import_module(primary)
    except ModuleNotFoundError as error:
        if error.name != "expra_connect.remote":
            raise
        return importlib.import_module(legacy)


_persistence = importlib.import_module("expra_connect.persistence")
MessagePackStateStore = getattr(_persistence, "MessagePackStateStore", None)
YamlStateStore = getattr(_persistence, "YamlStateStore", None)
_wire_protocol = _import_version_module(
    "expra_connect.remote.wire_protocol", "expra_connect.wire_protocol"
)
_session = _import_version_module(
    "expra_connect.remote.session", "expra_connect.session"
)
RemoteRequest: Any = _wire_protocol.RemoteRequest
ReplayCache = _wire_protocol.ReplayCache
sign_request = _wire_protocol.sign_request
sign_response = _wire_protocol.sign_response
verify_request = _wire_protocol.verify_request
verify_response = _wire_protocol.verify_response
LogicalSessionRegistry = _session.LogicalSessionRegistry

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
    setup: Callable[[], None] | None = None


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


REPLAY_CACHE_CAPACITY = 4096
_REPLAY_CACHE = ReplayCache(clock=time.monotonic, max_entries=REPLAY_CACHE_CAPACITY)


def _reset_replay_cache() -> None:
    global _REPLAY_CACHE
    _REPLAY_CACHE = ReplayCache(clock=time.monotonic, max_entries=REPLAY_CACHE_CAPACITY)


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


def _bench_yaml_save(store: Any) -> Callable[[], None]:
    def _run() -> None:
        store.save(_YAML_PAYLOAD)

    return _run


def _bench_yaml_load(store: Any) -> Callable[[], None]:
    def _run() -> None:
        store.load()

    return _run


def _bench_msgpack_save(store: Any) -> Callable[[], None]:
    def _run() -> None:
        store.save(_MSGPACK_PAYLOAD)

    return _run


def _bench_msgpack_load(store: Any) -> Callable[[], None]:
    def _run() -> None:
        store.load()

    return _run


# ---------------------------------------------------------------------------
# Workload C: Replay cache — bounded check-and-record
# ---------------------------------------------------------------------------

_SMALL_CACHE = ReplayCache(clock=time.monotonic, max_entries=256)
_SMALL_CACHE_COUNTER = 0


def _reset_small_cache() -> None:
    global _SMALL_CACHE
    _SMALL_CACHE = ReplayCache(clock=time.monotonic, max_entries=256)
    seen_at = time.monotonic()
    for index in range(255):
        _SMALL_CACHE.check_and_record(
            NODE_A.value, f"setup-{index}", f"setup-nonce-{index}", seen_at
        )


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


def _make_anon_request(rid: str, nonce: str) -> Any:
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


def _reset_session_registry() -> None:
    global _SESSION_REG
    _SESSION_REG = LogicalSessionRegistry(clock=time.monotonic, max_sessions=2)


def _prepare_occupied_session_registry() -> None:
    global _SESSION_REG
    session_ids = iter(range(SESSION_STRESS_MAX_SESSIONS))

    def next_session_id() -> str:
        return f"benchmark-session-{next(session_ids)}"

    _SESSION_REG = LogicalSessionRegistry(
        clock=time.monotonic,
        max_sessions=SESSION_STRESS_MAX_SESSIONS,
        session_id_factory=next_session_id,
    )
    for index in range(SESSION_STRESS_ACTIVE_COUNT):
        _SESSION_REG.authenticate(
            _make_anon_request(f"setup-{index}", f"setup-nonce-{index}"),
            connection_generation="setup-generation",
        )


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
        "open_or_resume + assert_current (900 active sessions)",
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
        "open_or_resume + assert_current (900 active sessions)",
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
            _session.__name__,
            _wire_protocol.__name__,
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
        yaml_store = None
        if YamlStateStore is not None:
            yaml_store = YamlStateStore(yaml_path)
            yaml_store.save(_YAML_PAYLOAD)
        msgpack_store = None
        if MessagePackStateStore is not None:
            msgpack_store = MessagePackStateStore(msgpack_path)
            msgpack_store.save(_MSGPACK_PAYLOAD)
        state_fixture = {"shape": "five_peer_state", "peers": 5}
        operations: list[OperationSpec] = [
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
                {"shape": "ping_request", "replay_cache_occupancy": 0},
                1,
                stateful=True,
                setup=_reset_replay_cache,
            ),
            OperationSpec(
                "replay cache check_and_record",
                _bench_replay_cache,
                {
                    "shape": "new_record_at_capacity_boundary",
                    "occupancy_before": 255,
                    "max_entries": 256,
                },
                1,
                stateful=True,
                setup=_reset_small_cache,
            ),
            OperationSpec(
                "open_or_resume + assert_current",
                _bench_session_open_assert,
                {"shape": "anonymous_session"},
                1,
                stateful=True,
                setup=_reset_session_registry,
            ),
            OperationSpec(
                "open_or_resume + assert_current (900 active sessions)",
                _bench_session_open_assert,
                {
                    "shape": "anonymous_session_open_at_high_occupancy",
                    "active_sessions_before": SESSION_STRESS_ACTIVE_COUNT,
                    "max_sessions": SESSION_STRESS_MAX_SESSIONS,
                },
                1,
                stateful=True,
                warmups=10,
                repetitions=100,
                setup=_prepare_occupied_session_registry,
            ),
            OperationSpec(
                "NodeIdentity.create() [Ed25519 keygen]",
                _bench_identity_create,
                {"shape": "generated_ed25519_identity"},
                1,
                warmups=10,
                repetitions=200,
            ),
        ]
        if yaml_store is not None:
            operations.extend(
                (
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
                )
            )
        if msgpack_store is not None:
            operations.extend(
                (
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
                )
            )
        if yaml_store is None and (
            args.operation is None
            or args.operation
            in (
                "yaml save (5-peer payload)",
                "yaml load",
            )
        ):
            unavailable_names = (
                (args.operation,)
                if args.operation is not None
                else ("yaml save (5-peer payload)", "yaml load")
            )
            unavailable_config = MeasurementConfig(
                warmups=WARMUP if args.warmups is None else args.warmups,
                repetitions=MEASURE if args.repetitions is None else args.repetitions,
                batch_count=args.batch_count,
                track_allocations=args.track_allocations,
            )
            for name in unavailable_names:
                outcomes.append(
                    BenchmarkOutcome(
                        case_id=name,
                        operation=name,
                        fixture=state_fixture,
                        size=5,
                        payload_bytes=None,
                        config=unavailable_config,
                        unavailable_reason=(
                            f"YamlStateStore is not available in package {__version__}"
                        ),
                    )
                )
                print(f"{name}: unavailable (YamlStateStore API not present)")
        if msgpack_store is None and (
            args.operation is None
            or args.operation
            in (
                "msgpack save (5-peer payload)",
                "msgpack load",
            )
        ):
            unavailable_names = (
                (args.operation,)
                if args.operation is not None
                else ("msgpack save (5-peer payload)", "msgpack load")
            )
            unavailable_config = MeasurementConfig(
                warmups=WARMUP if args.warmups is None else args.warmups,
                repetitions=MEASURE if args.repetitions is None else args.repetitions,
                batch_count=args.batch_count,
                track_allocations=args.track_allocations,
            )
            for name in unavailable_names:
                outcomes.append(
                    BenchmarkOutcome(
                        case_id=name,
                        operation=name,
                        fixture=state_fixture,
                        size=5,
                        payload_bytes=None,
                        config=unavailable_config,
                        unavailable_reason=(
                            f"MessagePackStateStore is not available in package {__version__}"
                        ),
                    )
                )
                print(f"{name}: unavailable (MessagePackStateStore API not present)")
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
            result = measure(spec.operation, config, setup=spec.setup)
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

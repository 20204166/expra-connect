"""Host-level extended peer acceptance harness."""

from __future__ import annotations

import argparse
import json
import time
from contextlib import contextmanager
from dataclasses import FrozenInstanceError
from enum import Enum
from pathlib import Path
from threading import Lock
from typing import Any, Iterator

from expra_connect import (  # noqa: F401 - staged runtime imports are intentional
    ConnectConfig,
    ConnectRuntime,
    NodeId,
    __version__,
)

CAPABILITY = "test.read_state"
_REPORT_LOCK = Lock()

_EVENT_FIELDS: dict[str, frozenset[str]] = {
    "started": frozenset({"role", "version", "state"}),
    "target_ready": frozenset(),
    "stopping": frozenset(),
    "discovery_event": frozenset({"kind", "address", "port", "source"}),
    "discovered": frozenset({"address", "port", "source"}),
    "discovery_timeout": frozenset({"peers_count"}),
    "route_attempt": frozenset(
        {"phase", "outcome", "address", "port", "source", "duration_ms"}
    ),
    "pairing_request": frozenset({"caller_node_id", "permissions"}),
    "paired": frozenset({"peer_id", "permissions"}),
    "connected": frozenset({"peer_id", "tls_verified", "generation"}),
    "shared_capability_result": frozenset({"capability", "outcome"}),
    "shared_after_rotation": frozenset({"capability", "outcome"}),
    "error": frozenset({"error_type"}),
    "post_revoke_denied": frozenset({"error_type"}),
    "restored_trust": frozenset({"peer_id"}),
    "self_revoked": frozenset({"peer_id"}),
    "rotated": frozenset({"generation"}),
    "waiting_for_rotated_peer": frozenset({"peer_id", "timeout"}),
    "reconnected_after_rotation": frozenset({"peer_id"}),
    "diagnostics": frozenset({"generation", "routes_count", "connections_count"}),
    "surface_request": frozenset(
        {"surface_id", "access", "outcome", "error_type"}
    ),
    "surface_result": frozenset(
        {"surface_id", "access", "outcome", "error_type"}
    ),
    "surface_denied": frozenset(
        {"surface_id", "access", "outcome", "error_type"}
    ),
    "reverse_connected": frozenset(
        {"surface_id", "access", "outcome", "error_type"}
    ),
    "reverse_paired": frozenset(
        {"surface_id", "access", "outcome", "error_type"}
    ),
}


def json_default(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    return str(value)


def _field(value: Any) -> str:
    if value is None:
        return "-"
    if isinstance(value, Enum):
        return str(value.value)
    if isinstance(value, bool):
        return str(value).lower()
    return str(value)


def _status_state(status: Any) -> Any:
    state = getattr(status, "state", None)
    return getattr(state, "value", state)


def _safe_values(event: str, values: dict[str, Any]) -> dict[str, Any]:
    """Keep only fields explicitly approved for this event."""
    allowed = _EVENT_FIELDS.get(event, frozenset())
    return {key: values[key] for key in allowed if key in values}


def format_terminal_event(sequence: int, event: str, **values: Any) -> str:
    """Render an allowlisted, non-sensitive human-readable event line."""
    safe = _safe_values(event, values)
    prefix = f"[{sequence:02d}]"
    if event == "started":
        return (
            f"{prefix} started role={_field(safe.get('role'))} "
            f"version={_field(safe.get('version'))} "
            f"state={_field(safe.get('state'))}"
        )
    if event == "discovery_event":
        return f"{prefix} discovery kind={_field(safe.get('kind'))}"
    if event == "discovered":
        return (
            f"{prefix} discovered address={_field(safe.get('address'))} "
            f"port={_field(safe.get('port'))} source={_field(safe.get('source'))}"
        )
    if event == "discovery_timeout":
        return f"{prefix} discovery_timeout peers={_field(safe.get('peers_count'))}"
    if event == "route_attempt":
        line = (
            f"{prefix} route phase={_field(safe.get('phase'))} "
            f"outcome={_field(safe.get('outcome'))} address={_field(safe.get('address'))} "
            f"port={_field(safe.get('port'))} source={_field(safe.get('source'))}"
        )
        return (
            f"{line} latency_ms={safe['duration_ms']}"
            if "duration_ms" in safe
            else line
        )
    if event == "error":
        return f"{prefix} error type={_field(safe.get('error_type'))}"
    if event == "post_revoke_denied":
        return f"{prefix} post_revoke_denied type={_field(safe.get('error_type'))}"
    if event == "target_ready":
        return f"{prefix} target_ready"
    if event == "stopping":
        return f"{prefix} stopping"
    if event == "diagnostics":
        return (
            f"{prefix} diagnostics generation={_field(safe.get('generation'))} "
            f"routes={_field(safe.get('routes_count'))} "
            f"connections={_field(safe.get('connections_count'))}"
        )
    if event in {
        "surface_request",
        "surface_result",
        "surface_denied",
        "reverse_connected",
        "reverse_paired",
    }:
        line = (
            f"{prefix} {event} surface_id={_field(safe.get('surface_id'))} "
            f"access={_field(safe.get('access'))} "
            f"outcome={_field(safe.get('outcome'))}"
        )
        return (
            f"{line} error_type={_field(safe.get('error_type'))}"
            if "error_type" in safe
            else line
        )
    return f"{prefix} {event}"


def _next_sequence(history: list[dict[str, Any]]) -> int:
    sequences = [
        record.get("sequence")
        for record in history
        if isinstance(record.get("sequence"), int)
    ]
    return max(sequences, default=0) + 1


def write_event(report: Path, event: str, **values: Any) -> None:
    """Append one ordered, redacted event to the report and terminal output."""
    with _REPORT_LOCK:
        history: list[dict[str, Any]] = []
        if report.exists():
            try:
                existing = json.loads(report.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                existing = []
            if isinstance(existing, list) and all(
                isinstance(item, dict) for item in existing
            ):
                history = existing

        sequence = _next_sequence(history)
        safe = _safe_values(event, values)
        record = {"event": event, "sequence": sequence, **safe}
        history.append(record)
        report.parent.mkdir(parents=True, exist_ok=True)
        report.write_text(
            json.dumps(history, indent=2, sort_keys=True, default=json_default) + "\n",
            encoding="utf-8",
        )
        print(format_terminal_event(sequence, event, **safe), flush=True)


def register_harness_surfaces(runtime: ConnectRuntime) -> None:
    """Register deterministic structured surfaces without exposing payloads."""
    surfaces = (
        (
            "desktop",
            {"surface_id": "desktop", "state": "ready"},
            {"surface_id": "desktop", "review": "ready"},
        ),
        (
            "desktop/settings",
            {"surface_id": "desktop/settings", "theme": "light"},
            {"surface_id": "desktop/settings", "review": "stable"},
        ),
        (
            "device/status",
            {"surface_id": "device/status", "status": "online"},
            {"surface_id": "device/status", "review": "healthy"},
        ),
    )
    for surface_id, read_result, review_result in surfaces:
        def read(_peer_id: Any, _params: dict[str, Any], result=read_result) -> dict[str, Any]:
            return dict(result)

        def review(
            _peer_id: Any, _params: dict[str, Any], result=review_result
        ) -> dict[str, Any]:
            return dict(result)

        def save(_peer_id: Any, _params: dict[str, Any], name=surface_id) -> dict[str, Any]:
            return {"surface_id": name, "saved": True}

        runtime.register_surface(
            surface_id,
            read=read,
            review=review,
            actions={"save": save},
        )


def record_surface_result(
    report: Path,
    surface_id: str,
    access: str,
    outcome: str,
    error_type: str | None = None,
) -> None:
    """Record surface metadata only; handler results are intentionally omitted."""
    values: dict[str, Any] = {
        "surface_id": surface_id,
        "access": access,
        "outcome": outcome,
    }
    if error_type is not None:
        values["error_type"] = error_type
    write_event(report, "surface_result", **values)


@contextmanager
def cleanup_runtime(runtime: ConnectRuntime) -> Iterator[ConnectRuntime]:
    """Provide the common shutdown boundary for later runtime stages."""
    try:
        yield runtime
    finally:
        runtime.shutdown()


def _candidate_values(candidate: Any) -> dict[str, Any]:
    endpoints = getattr(candidate, "endpoint_candidates", ()) or ()
    endpoint = endpoints[0] if isinstance(endpoints, (list, tuple)) and endpoints else None
    addresses = getattr(candidate, "addresses", ()) or ()
    if not isinstance(addresses, (list, tuple)):
        addresses = ()
    address = getattr(endpoint, "address", None) or next(
        iter(addresses), None
    )
    port = getattr(endpoint, "port", None) or getattr(candidate, "port", None)
    source = getattr(endpoint, "source", None)
    if source is None:
        source = "discovery"
    return {
        "address": address,
        "port": port,
        "source": source,
    }


def _write_started(report: Path, role: str, runtime: ConnectRuntime, status: Any) -> None:
    identity = runtime.identity
    if identity is None:
        raise RuntimeError("runtime identity is unavailable")
    state = getattr(status, "state", None)
    write_event(
        report,
        "started",
        role=role,
        version=__version__,
        state=getattr(state, "value", state),
    )


def wait_for_peer(
    runtime: ConnectRuntime,
    peer_id: str | None,
    timeout: float,
    *,
    generation_not: int | None = None,
    fingerprint_not: str | None = None,
) -> Any | None:
    """Wait for a matching public discovery candidate without fixed delays."""
    deadline = time.monotonic() + max(timeout, 0.0)
    while True:
        candidate = next(
            (
                peer
                for peer in runtime.peers
                if peer_id is None or peer.stable_id == peer_id
                if generation_not is None
                or getattr(peer, "transport_generation", None) != generation_not
                if fingerprint_not is None
                or getattr(peer, "transport_fingerprint", None) != fingerprint_not
            ),
            None,
        )
        if candidate is not None:
            return candidate
        if time.monotonic() >= deadline:
            return None
        time.sleep(min(0.1, max(0.0, deadline - time.monotonic())))


def _diagnostics_values(diagnostics: Any) -> dict[str, Any]:
    if not isinstance(diagnostics, dict):
        return {}
    return {
        "generation": diagnostics.get("generation"),
        "routes_count": len(diagnostics.get("routes", ()))
        if isinstance(diagnostics.get("routes"), (list, tuple))
        else None,
        "connections_count": len(diagnostics.get("connections", ()))
        if isinstance(diagnostics.get("connections"), (list, tuple))
        else None,
    }


def run_target(args: Any, runtime: ConnectRuntime) -> int:
    """Start a target, expose the test capability, and remain available."""
    def approve_pairing(request: Any) -> bool:
        permissions = sorted(
            getattr(permission, "value", permission)
            for permission in getattr(request, "permissions", ())
        )
        write_event(
            args.report,
            "pairing_request",
            caller_node_id=getattr(getattr(request, "caller_node_id", None), "value", None),
            permissions=permissions,
        )
        runtime.sharing.allow(request.caller_node_id, CAPABILITY)
        return True

    runtime.sharing.register(
        CAPABILITY,
        lambda peer_id, params: {"ok": True, "peer_id": peer_id.value, "params": params},
    )
    try:
        setattr(runtime.config, "on_pairing_request", approve_pairing)
    except FrozenInstanceError:
        # The real public config is frozen; its callback is installed at build time.
        pass
    try:
        status = runtime.start()
        _write_started(args.report, "target", runtime, status)
        if _status_state(status) != "started":
            raise RuntimeError("runtime did not start")
        write_event(args.report, "target_ready")
        rotate_after = getattr(args, "rotate_after", None)
        rotation_deadline = (
            time.monotonic() + max(rotate_after, 0.0)
            if rotate_after is not None
            else None
        )
        rotated = False
        deadline = time.monotonic() + max(args.wait, 0.0)
        while time.monotonic() < deadline:
            if rotation_deadline is not None and not rotated and time.monotonic() >= rotation_deadline:
                runtime.rotate_transport()
                generations = runtime.transport_generations
                write_event(
                    args.report,
                    "rotated",
                    generation=(
                        generations.current_generation if generations is not None else None
                    ),
                )
                rotated = True
            time.sleep(min(0.1, deadline - time.monotonic()))
        if rotation_deadline is not None and not rotated:
            runtime.rotate_transport()
            generations = runtime.transport_generations
            write_event(
                args.report,
                "rotated",
                generation=(
                    generations.current_generation if generations is not None else None
                ),
            )
        return 0
    except Exception as error:  # noqa: BLE001 - report only the exception type
        write_event(args.report, "error", error_type=type(error).__name__)
        return 1
    finally:
        runtime.shutdown()


def run_initiator(
    args: Any,
    runtime: ConnectRuntime,
    runtime_factory: Any | None = None,
) -> int:
    """Run the ordered initiator discovery, trust, connection, and share stages."""
    try:
        status = runtime.start()
        _write_started(args.report, "initiator", runtime, status)
        if _status_state(status) != "started":
            raise RuntimeError("runtime did not start")
        candidate = wait_for_peer(runtime, args.peer_id, args.wait)
        if candidate is None:
            write_event(args.report, "discovery_timeout", peers_count=len(runtime.peers))
            return 2
        route = _candidate_values(candidate)
        write_event(args.report, "discovered", **route)
        peer_id = NodeId(candidate.stable_id)
        trusted = runtime.pairing.trusted.get(peer_id) if runtime.pairing else None
        if trusted is None:
            trusted = runtime.pair_peer(peer_id)
            write_event(
                args.report,
                "paired",
                peer_id=trusted.peer_id.value,
                permissions=sorted(trusted.permissions),
            )
        else:
            write_event(args.report, "restored_trust", peer_id=peer_id.value)
        provider = runtime.connect_peer(peer_id)
        write_event(
            args.report,
            "connected",
            peer_id=peer_id.value,
            tls_verified=True,
            generation=getattr(candidate, "transport_generation", None),
        )
        result = provider.request_shared(
            CAPABILITY, {"source": "run_peer_extended.py"}
        )
        write_event(
            args.report,
            "shared_capability_result",
            capability=CAPABILITY,
            outcome="success" if isinstance(result, dict) and result.get("ok") is True else "failure",
        )
        if getattr(args, "reconnect_after_rotation", False):
            write_event(
                args.report,
                "waiting_for_rotated_peer",
                peer_id=peer_id.value,
                timeout=args.rotation_wait,
            )
            previous_generation = getattr(candidate, "transport_generation", None)
            previous_fingerprint = getattr(candidate, "transport_fingerprint", None)
            rotated_candidate = wait_for_peer(
                runtime,
                peer_id.value,
                args.rotation_wait,
                generation_not=previous_generation,
                fingerprint_not=previous_fingerprint,
            )
            if rotated_candidate is None:
                raise TimeoutError("rotated peer was not rediscovered")
            if (
                getattr(rotated_candidate, "transport_generation", None)
                == previous_generation
            ):
                raise RuntimeError("transport generation did not change")
            provider = runtime.reconnect_peer(peer_id)
            write_event(args.report, "reconnected_after_rotation", peer_id=peer_id.value)
            result = provider.request_shared(
                CAPABILITY, {"source": "run_peer_extended.py", "after_rotation": True}
            )
            write_event(
                args.report,
                "shared_after_rotation",
                capability=CAPABILITY,
                outcome=(
                    "success"
                    if isinstance(result, dict) and result.get("ok") is True
                    else "failure"
                ),
            )
        if getattr(args, "revoke_self", False):
            provider.revoke_self()
            write_event(args.report, "self_revoked", peer_id=peer_id.value)
            try:
                provider.request_shared(CAPABILITY, {"source": "after_revoke"})
            except Exception as error:  # noqa: BLE001 - report only the exception type
                write_event(
                    args.report,
                    "post_revoke_denied",
                    error_type=type(error).__name__,
                )
            else:
                raise AssertionError("revoked caller still accessed capability")
        if getattr(args, "restart_check", False):
            if runtime_factory is None:
                raise RuntimeError("restart runtime factory is unavailable")
            runtime.shutdown()
            runtime = runtime_factory()
            status = runtime.start()
            if _status_state(status) != "started":
                raise RuntimeError("runtime did not restart")
            restored = runtime.pairing and runtime.pairing.trusted.get(peer_id)
            if restored is None:
                raise RuntimeError("trusted peer was not restored")
            write_event(args.report, "restored_trust", peer_id=peer_id.value)
        write_event(args.report, "diagnostics", **_diagnostics_values(runtime.diagnostics()))
        return 0
    except Exception as error:  # noqa: BLE001 - report only the exception type
        write_event(args.report, "error", error_type=type(error).__name__)
        return 1
    finally:
        runtime.shutdown()


def _runtime(args: Any) -> ConnectRuntime:
    route_started: dict[tuple[str, str, int], float] = {}
    runtime_holder: list[ConnectRuntime] = []

    def route_attempt(phase: str, endpoint: Any, outcome: str, _error: str | None) -> None:
        key = (phase, endpoint.address, endpoint.port)
        now = time.monotonic()
        duration_ms = None
        if outcome == "started":
            route_started[key] = now
        elif key in route_started:
            duration_ms = round((now - route_started.pop(key)) * 1000, 1)
        write_event(
            args.report,
            "route_attempt",
            phase=phase,
            address=endpoint.address,
            port=endpoint.port,
            source=getattr(endpoint.source, "value", endpoint.source),
            outcome=outcome,
            duration_ms=duration_ms,
        )

    def approve_pairing(request: Any) -> bool:
        runtime = runtime_holder[0]
        permissions = sorted(
            getattr(permission, "value", permission)
            for permission in getattr(request, "permissions", ())
        )
        write_event(
            args.report,
            "pairing_request",
            caller_node_id=getattr(getattr(request, "caller_node_id", None), "value", None),
            permissions=permissions,
        )
        runtime.sharing.allow(request.caller_node_id, CAPABILITY)
        return True

    runtime = ConnectRuntime(
        ConnectConfig(
            profile_dir=args.profile,
            advertised_addresses=(tuple(args.advertise_address) if args.advertise_address else None),
            on_route_attempt=route_attempt,
            on_pairing_request=approve_pairing if args.role == "target" else None,
        )
    )
    runtime_holder.append(runtime)
    return runtime


def _runtime_factory(args: Any) -> Any:
    return lambda: _runtime(args)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--role", choices=("target", "initiator"), required=True)
    parser.add_argument("--profile", type=Path, required=True)
    parser.add_argument(
        "--report", type=Path, default=Path("peer-extended-report.json")
    )
    parser.add_argument("--peer-id")
    parser.add_argument("--wait", type=float, default=60.0)
    parser.add_argument("--advertise-address", action="append")
    parser.add_argument("--bidirectional-surfaces", action="store_true")
    parser.add_argument("--rotate-after", type=float)
    parser.add_argument("--reconnect-after-rotation", action="store_true")
    parser.add_argument("--rotation-wait", type=float, default=15.0)
    parser.add_argument("--restart-check", action="store_true")
    parser.add_argument("--revoke-self", action="store_true")
    return parser


def main() -> int:
    args = _parser().parse_args()
    runtime = _runtime(args)
    return (
        run_target(args, runtime)
        if args.role == "target"
        else run_initiator(args, runtime, runtime_factory=_runtime_factory(args))
    )


if __name__ == "__main__":
    raise SystemExit(main())

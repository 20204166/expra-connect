"""Two-node acceptance harness for Expra Connect.

This is deliberately a host-level test runner, not part of the package API.
It prints ordered redacted event summaries and writes structured reports to disk.
"""

from __future__ import annotations

import argparse
import json
import time
from dataclasses import asdict, is_dataclass
from enum import Enum
from pathlib import Path
from threading import Lock
from typing import Any

from expra_connect import ConnectConfig, ConnectRuntime, NodeId, __version__
from expra_connect.wire_protocol import RemoteAuthorizationError

CAPABILITY = "test.read_state"
_REPORT_LOCK = Lock()
_TERMINAL_SEQUENCE = 0


def _reallow_capability_after_rotation(
    runtime: ConnectRuntime, peer_ids: set[NodeId]
) -> None:
    """Re-apply only the harness's explicit grants after transport restart."""
    for peer_id in peer_ids:
        runtime.sharing.allow(peer_id, CAPABILITY)


def _retry_shared_capability(operation: Any, timeout: float = 5.0) -> Any:
    """Wait briefly for a target harness to re-apply its explicit grant."""
    deadline = time.monotonic() + max(timeout, 0.0)
    while True:
        try:
            return operation()
        except RemoteAuthorizationError:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise
            time.sleep(min(0.1, remaining))


def json_default(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    return str(value)


def _short(value: Any) -> str:
    text = str(value) if value is not None else "-"
    return text if len(text) <= 8 else f"{text[:8]}..."


def _field(value: Any) -> str:
    if value is None:
        return "-"
    if isinstance(value, Enum):
        return str(value.value)
    if isinstance(value, bool):
        return str(value).lower()
    return str(value)


def _candidate_fields(payload: Any) -> tuple[str, str, str]:
    if not isinstance(payload, dict):
        return "-", "-", "-"
    addresses = payload.get("addresses") or []
    endpoints = payload.get("endpoint_candidates") or []
    endpoint = endpoints[0] if endpoints and isinstance(endpoints[0], dict) else {}
    return (
        _field(addresses[0] if addresses else endpoint.get("address")),
        _field(payload.get("port") or endpoint.get("port")),
        _field(endpoint.get("source")),
    )


def format_terminal_event(sequence: int, event: str, **values: Any) -> str:
    """Render one stable, useful, non-sensitive terminal event line."""

    prefix = f"[{sequence:02d}]"
    if event == "started":
        return (
            f"{prefix} started role={_field(values.get('role'))} "
            f"version={_field(values.get('version'))} "
            f"state={_field(values.get('state'))}"
        )
    if event == "discovery_event" and values.get("kind") == "candidate":
        address, port, source = _candidate_fields(values.get("payload"))
        return f"{prefix} discovered address={address} port={port} source={source}"
    if event == "discovered":
        address, port, source = _candidate_fields(values.get("candidate"))
        return f"{prefix} discovered address={address} port={port} source={source}"
    if event == "discovery_event" and values.get("kind") == "lost":
        return f"{prefix} discovery lost peer={_short(values.get('payload'))}"
    if event == "discovery_event":
        return f"{prefix} discovery kind={_field(values.get('kind'))}"
    if event == "route_attempt":
        line = (
            f"{prefix} route phase={_field(values.get('phase'))} "
            f"outcome={_field(values.get('outcome'))} "
            f"address={_field(values.get('address'))} "
            f"port={_field(values.get('port'))} "
            f"source={_field(values.get('source'))}"
        )
        duration = values.get("duration_ms")
        return f"{line} latency_ms={duration}" if duration is not None else line
    if event == "pairing_request":
        permissions = ",".join(sorted(values.get("permissions") or ())) or "-"
        return (
            f"{prefix} pairing_request caller={_short(values.get('caller_node_id'))} "
            f"permissions={permissions}"
        )
    if event == "paired":
        permissions = ",".join(sorted(values.get("permissions") or ())) or "-"
        return (
            f"{prefix} paired peer={_short(values.get('peer_id'))} "
            f"permissions={permissions}"
        )
    if event == "connected":
        return (
            f"{prefix} connected peer={_short(values.get('peer_id'))} "
            f"tls_verified={_field(values.get('tls_verified', True))} "
            f"generation={_field(values.get('generation'))}"
        )
    if event in {"shared_capability_result", "shared_after_rotation"}:
        result = values.get("result")
        success = isinstance(result, dict) and result.get("ok") is True
        return (
            f"{prefix} shared capability={_field(values.get('capability', CAPABILITY))} "
            f"outcome={'success' if success else 'failure'}"
        )
    if event == "discovery_timeout":
        return f"{prefix} discovery_timeout peers={len(values.get('peers') or ())}"
    if event == "error":
        return f"{prefix} error type={_field(values.get('error_type'))}"
    if event == "post_revoke_denied":
        return f"{prefix} post_revoke_denied type={_field(values.get('error_type'))}"
    if event == "target_ready":
        return f"{prefix} target_ready"
    if event == "stopping":
        return f"{prefix} stopping"
    if event == "restored_trust":
        return f"{prefix} restored_trust peer={_short(values.get('peer_id'))}"
    if event == "self_revoked":
        return f"{prefix} self_revoked peer={_short(values.get('peer_id'))}"
    if event == "rotated":
        return f"{prefix} rotated generation={_field(values.get('generation'))}"
    if event == "waiting_for_rotated_peer":
        return (
            f"{prefix} waiting_for_rotated_peer peer={_short(values.get('peer_id'))} "
            f"timeout={_field(values.get('timeout'))}"
        )
    if event == "reconnected_after_rotation":
        return f"{prefix} reconnected_after_rotation peer={_short(values.get('peer_id'))}"
    return f"{prefix} {event}"


def write_event(report_path: Path, event: str, **values: Any) -> None:
    global _TERMINAL_SEQUENCE
    with _REPORT_LOCK:
        record = {"event": event, **values}
        _TERMINAL_SEQUENCE += 1
        print(format_terminal_event(_TERMINAL_SEQUENCE, event, **values), flush=True)
        history: list[dict[str, Any]] = []
        if report_path.exists():
            try:
                existing = json.loads(report_path.read_text(encoding="utf-8"))
                if isinstance(existing, list):
                    history = existing
            except json.JSONDecodeError:
                pass
        history.append(record)
        report_path.write_text(
            json.dumps(history, indent=2, sort_keys=True, default=json_default)
            + "\n",
            encoding="utf-8",
        )


def discovery_event(report_path: Path, kind: str, payload: Any) -> None:
    if is_dataclass(payload):
        payload = asdict(payload)
    write_event(report_path, "discovery_event", kind=kind, payload=payload)


def wait_for_peer(
    runtime: ConnectRuntime,
    peer_id: str | None,
    timeout: float,
    fingerprint_not: str | None = None,
) -> Any | None:
    """Wait for a current candidate instead of guessing a propagation delay."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        candidate = next(
            (
                peer
                for peer in runtime.peers
                if (peer_id is None or peer.stable_id == peer_id)
                and (
                    fingerprint_not is None
                    or peer.transport_fingerprint != fingerprint_not
                )
            ),
            None,
        )
        if candidate is not None:
            return candidate
        time.sleep(1)
    return None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", type=Path, required=True)
    parser.add_argument("--role", choices=("target", "initiator"), required=True)
    parser.add_argument("--peer-id", help="target peer node ID for initiator mode")
    parser.add_argument(
        "--existing-peer-id",
        help="reuse an already trusted peer after restarting the profile",
    )
    parser.add_argument(
        "--advertise-address",
        action="append",
        help="explicit address to advertise; repeat for multiple interfaces",
    )
    parser.add_argument(
        "--rotate-after",
        type=float,
        help="target-only: rotate transport after this many seconds",
    )
    parser.add_argument(
        "--reconnect-after-rotation",
        action="store_true",
        help="initiator-only: reconnect and share again after rotation",
    )
    parser.add_argument(
        "--rotation-wait",
        type=float,
        default=15.0,
        help="seconds to wait before reconnecting after a target rotation",
    )
    parser.add_argument(
        "--revoke-self",
        action="store_true",
        help="initiator-only: revoke this caller on the target and verify denial",
    )
    parser.add_argument("--wait", type=float, default=60.0)
    parser.add_argument("--report", type=Path, default=Path("peer-report.json"))
    args = parser.parse_args()

    runtime: ConnectRuntime
    route_started: dict[tuple[str, str, int], float] = {}
    capability_peers: set[NodeId] = set()

    def approve_pairing(request: Any) -> bool:
        write_event(
            args.report,
            "pairing_request",
            caller_node_id=request.caller_node_id.value,
            permissions=sorted(permission.value for permission in request.permissions),
        )
        runtime.sharing.allow(request.caller_node_id, CAPABILITY)
        capability_peers.add(request.caller_node_id)
        return True

    def route_attempt(
        phase: str, endpoint: Any, outcome: str, error: str | None
    ) -> None:
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
            source=endpoint.source.value,
            outcome=outcome,
            error=error,
            duration_ms=duration_ms,
        )

    runtime = ConnectRuntime(
        ConnectConfig(
            profile_dir=args.profile,
            on_pairing_request=approve_pairing if args.role == "target" else None,
            on_discovery=lambda kind, payload: discovery_event(
                args.report, kind, payload
            ),
            advertised_addresses=(
                tuple(args.advertise_address) if args.advertise_address else None
            ),
            on_route_attempt=route_attempt,
            discovery_enabled=True,
        )
    )
    if args.role == "target":
        runtime.sharing.register(
            CAPABILITY,
            lambda peer_id, params: {
                "ok": True,
                "peer_id": peer_id.value,
                "params": params,
            },
        )
    status = runtime.start()
    identity = runtime.identity
    if identity is None:
        raise RuntimeError("runtime did not create an identity")
    write_event(
        args.report,
        "started",
        role=args.role,
        version=__version__,
        node_id=identity.node_id.value,
        state=status.state.value,
        discovery_started=status.discovery_started,
        discovery_disabled=status.discovery_disabled,
        discovery_reason=status.discovery_reason,
        bound_host=status.bound_host,
        bound_port=status.bound_port,
        tls_fingerprint=status.tls_fingerprint,
    )

    try:
        if args.role == "target":
            write_event(
                args.report,
                "target_ready",
                instruction="Run the initiator harness with this node ID visible to it.",
            )
            rotation_deadline = (
                time.monotonic() + args.rotate_after
                if args.rotate_after is not None
                else None
            )
            while True:
                if rotation_deadline is not None and time.monotonic() >= rotation_deadline:
                    rotated = runtime.rotate_transport()
                    _reallow_capability_after_rotation(runtime, capability_peers)
                    write_event(
                        args.report,
                        "rotated",
                        generation=runtime.transport_generations.current_generation
                        if runtime.transport_generations is not None
                        else None,
                        tls_fingerprint=rotated.tls_fingerprint,
                    )
                    rotation_deadline = None
                time.sleep(1)

        candidate = wait_for_peer(runtime, args.peer_id, args.wait)
        if candidate is None:
            write_event(
                args.report,
                "discovery_timeout",
                peers=[asdict(peer) for peer in runtime.peers],
            )
            return 2

        write_event(args.report, "discovered", candidate=asdict(candidate))
        peer_id = NodeId(candidate.stable_id)
        if args.existing_peer_id:
            if args.existing_peer_id != peer_id.value:
                raise ValueError("discovered peer does not match --existing-peer-id")
            trusted = runtime.pairing.trusted.get(peer_id) if runtime.pairing else None
            if trusted is None:
                raise PermissionError("existing peer is not present in persisted trust")
            write_event(args.report, "restored_trust", peer_id=peer_id.value)
        else:
            trusted = runtime.pair_peer(peer_id)
            write_event(
                args.report,
                "paired",
                peer_id=trusted.peer_id.value,
                permissions=sorted(trusted.permissions),
            )
        provider = runtime.connect_peer(peer_id)
        write_event(
            args.report,
            "connected",
            peer_id=peer_id.value,
            tls_verified=True,
            generation=candidate.transport_generation,
        )
        result = provider.request_shared(CAPABILITY, {"source": "run_peer.py"})
        write_event(
            args.report,
            "shared_capability_result",
            capability=CAPABILITY,
            result=result,
        )
        if args.reconnect_after_rotation:
            write_event(
                args.report,
                "waiting_for_rotated_peer",
                peer_id=peer_id.value,
                timeout=args.rotation_wait,
            )
            candidate = wait_for_peer(
                runtime,
                peer_id.value,
                args.rotation_wait,
                fingerprint_not=candidate.transport_fingerprint,
            )
            if candidate is None:
                raise TimeoutError("rotated peer advertisement was not rediscovered")
            provider = runtime.reconnect_peer(peer_id)
            write_event(args.report, "reconnected_after_rotation", peer_id=peer_id.value)
            result = _retry_shared_capability(
                lambda: provider.request_shared(
                    CAPABILITY, {"source": "run_peer.py", "after_rotation": True}
                )
            )
            write_event(args.report, "shared_after_rotation", result=result)
        if args.revoke_self:
            provider.revoke_self()
            write_event(args.report, "self_revoked", peer_id=peer_id.value)
            try:
                provider.request_shared(CAPABILITY, {"source": "after_revoke"})
            except Exception as error:  # noqa: BLE001 - prove denial at the boundary
                write_event(
                    args.report,
                    "post_revoke_denied",
                    error_type=type(error).__name__,
                    error=str(error),
                )
            else:
                raise AssertionError("revoked caller still accessed capability")
        return 0
    except Exception as error:  # noqa: BLE001 - report every host-level failure
        write_event(
            args.report,
            "error",
            error_type=type(error).__name__,
            error=str(error),
        )
        return 1
    finally:
        runtime.shutdown()


if __name__ == "__main__":
    raise SystemExit(main())

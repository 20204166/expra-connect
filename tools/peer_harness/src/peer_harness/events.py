"""Ordered, allowlisted, redacted event reporting for the peer harness."""

from __future__ import annotations

import json
from dataclasses import asdict, is_dataclass
from enum import Enum
from pathlib import Path
from threading import Lock
from typing import Any

CAPABILITY = "test.read_state"

_REPORT_LOCK = Lock()

_EVENT_FIELDS: dict[str, frozenset[str]] = {
    "started": frozenset(
        {
            "role",
            "version",
            "state",
            "node_id",
            "discovery_started",
            "discovery_disabled",
            "discovery_reason",
            "bound_host",
            "bound_port",
            "tls_fingerprint",
        }
    ),
    "target_ready": frozenset(),
    "stopping": frozenset(),
    "discovery_event": frozenset({"kind", "address", "port", "source"}),
    "discovered": frozenset({"address", "port", "source"}),
    "discovery_timeout": frozenset({"peers_count"}),
    "route_attempt": frozenset(
        {"phase", "outcome", "address", "port", "source", "duration_ms"}
    ),
    "pairing_request": frozenset({"caller_node_id", "permissions"}),
    "pairing_pending": frozenset({"caller_node_id", "transaction_id"}),
    "pairing_approved": frozenset({"caller_node_id", "transaction_id"}),
    "pairing_denied": frozenset({"caller_node_id", "transaction_id"}),
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
    "surface_request": frozenset({"surface_id", "access", "outcome", "error_type"}),
    "surface_result": frozenset({"surface_id", "access", "outcome", "error_type"}),
    "surface_denied": frozenset({"surface_id", "access", "outcome", "error_type"}),
    "reverse_connected": frozenset({"outcome", "error_type"}),
    "reverse_paired": frozenset({"outcome", "error_type"}),
}


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


def _safe_values(event: str, values: dict[str, Any]) -> dict[str, Any]:
    """Keep only fields explicitly approved for this event."""
    allowed = _EVENT_FIELDS.get(event, frozenset())
    return {key: values[key] for key in allowed if key in values}


def format_terminal_event(sequence: int, event: str, **values: Any) -> str:
    """Render one stable, useful, non-sensitive terminal event line."""
    safe = _safe_values(event, values)
    prefix = f"[{sequence:02d}]"
    if event == "started":
        return (
            f"{prefix} started role={_field(safe.get('role'))} "
            f"version={_field(safe.get('version'))} "
            f"state={_field(safe.get('state'))}"
        )
    if event == "discovery_event":
        if values.get("kind") == "candidate":
            if "payload" in values:
                address, port, source = _candidate_fields(values.get("payload"))
            else:
                address = _field(safe.get("address"))
                port = _field(safe.get("port"))
                source = _field(safe.get("source"))
            return f"{prefix} discovered address={address} port={port} source={source}"
        if values.get("kind") == "lost":
            return f"{prefix} discovery lost peer={_short(values.get('payload'))}"
        return f"{prefix} discovery kind={_field(safe.get('kind'))}"
    if event == "discovered":
        return (
            f"{prefix} discovered address={_field(safe.get('address'))} "
            f"port={_field(safe.get('port'))} source={_field(safe.get('source'))}"
        )
    if event == "discovery_timeout":
        if "peers_count" in values:
            count: Any = values.get("peers_count")
        else:
            count = len(values.get("peers") or ())
        return f"{prefix} discovery_timeout peers={_field(count)}"
    if event == "route_attempt":
        line = (
            f"{prefix} route phase={_field(safe.get('phase'))} "
            f"outcome={_field(safe.get('outcome'))} "
            f"address={_field(safe.get('address'))} "
            f"port={_field(safe.get('port'))} "
            f"source={_field(safe.get('source'))}"
        )
        duration = safe.get("duration_ms")
        return f"{line} latency_ms={duration}" if duration is not None else line
    if event == "pairing_request":
        permissions = ",".join(sorted(safe.get("permissions") or ())) or "-"
        return (
            f"{prefix} pairing_request caller={_short(safe.get('caller_node_id'))} "
            f"permissions={permissions}"
        )
    if event == "paired":
        permissions = ",".join(sorted(safe.get("permissions") or ())) or "-"
        return (
            f"{prefix} paired peer={_short(safe.get('peer_id'))} "
            f"permissions={permissions}"
        )
    if event == "connected":
        return (
            f"{prefix} connected peer={_short(safe.get('peer_id'))} "
            f"tls_verified={_field(safe.get('tls_verified', True))} "
            f"generation={_field(safe.get('generation'))}"
        )
    if event in {"shared_capability_result", "shared_after_rotation"}:
        outcome = safe.get("outcome")
        if outcome is None:
            result = values.get("result")
            outcome = (
                "success"
                if isinstance(result, dict) and result.get("ok") is True
                else "failure"
            )
        capability = _field(safe.get("capability", CAPABILITY))
        return f"{prefix} shared capability={capability} outcome={outcome}"
    if event == "error":
        return f"{prefix} error type={_field(safe.get('error_type'))}"
    if event == "post_revoke_denied":
        return f"{prefix} post_revoke_denied type={_field(safe.get('error_type'))}"
    if event == "target_ready":
        return f"{prefix} target_ready"
    if event == "stopping":
        return f"{prefix} stopping"
    if event == "restored_trust":
        return f"{prefix} restored_trust peer={_short(safe.get('peer_id'))}"
    if event == "self_revoked":
        return f"{prefix} self_revoked peer={_short(safe.get('peer_id'))}"
    if event == "rotated":
        return f"{prefix} rotated generation={_field(safe.get('generation'))}"
    if event == "waiting_for_rotated_peer":
        return (
            f"{prefix} waiting_for_rotated_peer peer={_short(safe.get('peer_id'))} "
            f"timeout={_field(safe.get('timeout'))}"
        )
    if event == "reconnected_after_rotation":
        return f"{prefix} reconnected_after_rotation peer={_short(safe.get('peer_id'))}"
    if event == "diagnostics":
        return (
            f"{prefix} diagnostics generation={_field(safe.get('generation'))} "
            f"routes={_field(safe.get('routes_count'))} "
            f"connections={_field(safe.get('connections_count'))}"
        )
    if event in {"reverse_connected", "reverse_paired"}:
        line = f"{prefix} {event} outcome={_field(safe.get('outcome'))}"
        return (
            f"{line} error_type={_field(safe.get('error_type'))}"
            if "error_type" in safe
            else line
        )
    if event in {"surface_request", "surface_result", "surface_denied"}:
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
    sequences: list[int] = [
        record["sequence"]
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


def discovery_event(report_path: Path, kind: str, payload: Any) -> None:
    if is_dataclass(payload) and not isinstance(payload, type):
        payload = asdict(payload)
    if kind == "candidate":
        address, port, source = _candidate_fields(payload)
        write_event(
            report_path,
            "discovery_event",
            kind=kind,
            address=address,
            port=port,
            source=source,
        )
    else:
        write_event(report_path, "discovery_event", kind=kind)

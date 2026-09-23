#!/usr/bin/env python3
"""
Deterministic analyzer for Expra Connect JSON event logs.

- Standard library only.
- Same input -> same report (no current time, randomness, network, or file mtimes).
- Accepts one or more JSON files or directories.
- Splits appended logs into runs at each "started" event.
- Summarizes discovery, pairing, routes, TLS connections, capabilities, versions,
  and identity-continuity findings.
- Never prints fields that look like secrets/private keys/tokens/proofs.

Examples:
    python expra_connect_log_analyzer.py endpoint.json
    python expra_connect_log_analyzer.py ./reports
    python expra_connect_log_analyzer.py a.json b.json --json
    python expra_connect_log_analyzer.py ./reports --full-identifiers
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

SENSITIVE_FRAGMENTS = (
    "secret",
    "private_key",
    "private-key",
    "password",
    "token",
    "proof",
)

CANDIDATE_EVENTS = {"discovery_event", "discovered"}


def _stable_short(value: Any, full: bool = False) -> str | None:
    if value is None:
        return None
    text = str(value)
    if full or len(text) <= 16:
        return text
    return f"{text[:8]}…{text[-4:]}"


def _safe_scalar(value: Any) -> Any:
    if isinstance(value, float):
        if not math.isfinite(value):
            return str(value)
        return round(value, 3)
    return value


def _safe_dict(data: dict[str, Any], *, full_ids: bool = False) -> dict[str, Any]:
    """Return a deterministic, redacted shallow-ish copy for reporting."""
    out: dict[str, Any] = {}
    for key in sorted(data):
        lower = key.lower()
        if any(fragment in lower for fragment in SENSITIVE_FRAGMENTS):
            continue
        value = data[key]
        if key in {"node_id", "peer_id", "stable_id", "identity_fingerprint",
                   "transport_fingerprint", "tls_fingerprint", "service_name"}:
            out[key] = _stable_short(value, full_ids)
        elif isinstance(value, dict):
            out[key] = _safe_dict(value, full_ids=full_ids)
        elif isinstance(value, list):
            cleaned = []
            for item in value:
                if isinstance(item, dict):
                    cleaned.append(_safe_dict(item, full_ids=full_ids))
                else:
                    cleaned.append(_safe_scalar(item))
            out[key] = cleaned
        else:
            out[key] = _safe_scalar(value)
    return out


def _load_events(path: Path) -> list[dict[str, Any]]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except UnicodeDecodeError as exc:
        raise ValueError(f"{path}: not valid UTF-8") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(
            f"{path}: invalid JSON at line {exc.lineno}, column {exc.colno}"
        ) from exc

    if isinstance(raw, list):
        events = raw
    elif isinstance(raw, dict) and isinstance(raw.get("events"), list):
        events = raw["events"]
    elif isinstance(raw, dict):
        events = [raw]
    else:
        raise ValueError(f"{path}: root must be an object, event array, or object with events[]")

    result: list[dict[str, Any]] = []
    for index, item in enumerate(events):
        if not isinstance(item, dict):
            raise ValueError(f"{path}: event #{index + 1} is not an object")
        result.append(item)
    return result


def _split_runs(events: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    if not events:
        return []
    runs: list[list[dict[str, Any]]] = []
    current: list[dict[str, Any]] = []
    for event in events:
        if event.get("event") == "started" and current:
            runs.append(current)
            current = []
        current.append(event)
    if current:
        runs.append(current)
    return runs


def _candidate_from_event(event: dict[str, Any]) -> dict[str, Any] | None:
    if event.get("event") == "discovery_event":
        payload = event.get("payload")
        return payload if isinstance(payload, dict) else None
    if event.get("event") == "discovered":
        candidate = event.get("candidate")
        return candidate if isinstance(candidate, dict) else None
    return None


def _route_stats(events: list[dict[str, Any]]) -> dict[str, Any]:
    phases: dict[str, dict[str, Any]] = {}
    by_phase: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for event in events:
        if event.get("event") == "route_attempt":
            by_phase[str(event.get("phase") or "unknown")].append(event)

    for phase in sorted(by_phase):
        items = by_phase[phase]
        outcomes = Counter(str(item.get("outcome") or "unknown") for item in items)
        durations = [
            float(item["duration_ms"])
            for item in items
            if isinstance(item.get("duration_ms"), (int, float))
            and not isinstance(item.get("duration_ms"), bool)
            and math.isfinite(float(item["duration_ms"]))
        ]
        phases[phase] = {
            "attempt_events": len(items),
            "outcomes": dict(sorted(outcomes.items())),
            "successful_duration_ms": {
                "min": round(min(durations), 3),
                "max": round(max(durations), 3),
                "mean": round(sum(durations) / len(durations), 3),
            } if durations else None,
        }
    return phases


def _run_report(
    run_no: int,
    events: list[dict[str, Any]],
    *,
    full_ids: bool,
) -> dict[str, Any]:
    started = next((e for e in events if e.get("event") == "started"), None)
    event_counts = Counter(str(e.get("event") or "<missing>") for e in events)

    candidate_records: dict[str, dict[str, Any]] = {}
    candidate_observations = 0
    for event in events:
        candidate = _candidate_from_event(event)
        if candidate is None:
            continue
        candidate_observations += 1
        stable_id = candidate.get("stable_id")
        key = str(stable_id) if stable_id is not None else f"<unknown-{candidate_observations}>"
        candidate_records.setdefault(key, candidate)

    paired = [e for e in events if e.get("event") == "paired"]
    connected = [e for e in events if e.get("event") == "connected"]
    shared = [e for e in events if e.get("event") == "shared_capability_result"]

    findings: list[dict[str, str]] = []

    if started is None:
        findings.append({"severity": "warning", "code": "run_missing_started",
                         "message": "Run has no started event."})
    else:
        if started.get("state") != "started":
            findings.append({"severity": "warning", "code": "unexpected_start_state",
                             "message": f"Start state is {started.get('state')!r}, not 'started'."})
        if started.get("discovery_disabled") is True:
            findings.append({"severity": "info", "code": "discovery_disabled",
                             "message": "Discovery was explicitly disabled."})
        elif started.get("discovery_started") is not True:
            findings.append({"severity": "warning", "code": "discovery_not_started",
                             "message": "Discovery was expected but did not report started=true."})

    for stable_id, candidate in sorted(candidate_records.items()):
        if candidate.get("compatible") is False:
            findings.append({"severity": "warning", "code": "peer_incompatible",
                             "message": f"Peer {_stable_short(stable_id, full_ids)} is incompatible."})
        if candidate.get("connectable") is False:
            findings.append({"severity": "warning", "code": "peer_not_connectable",
                             "message": f"Peer {_stable_short(stable_id, full_ids)} is not connectable."})
        endpoints = candidate.get("endpoint_candidates")
        if isinstance(endpoints, list) and not endpoints:
            findings.append({"severity": "warning", "code": "peer_has_no_routes",
                             "message": f"Peer {_stable_short(stable_id, full_ids)} has no endpoint candidates."})

    for event in events:
        if event.get("event") == "route_attempt":
            if event.get("outcome") not in {"started", "succeeded"}:
                findings.append({
                    "severity": "warning",
                    "code": "route_attempt_failed",
                    "message": (
                        f"{event.get('phase', 'unknown')} route "
                        f"{event.get('address')}:{event.get('port')} -> {event.get('outcome')}"
                        + (f" ({event.get('error')})" if event.get("error") else "")
                    ),
                })

    for event in connected:
        if event.get("tls_verified") is not True:
            findings.append({"severity": "error", "code": "connection_not_tls_verified",
                             "message": "Connected event did not report tls_verified=true."})

    for event in shared:
        result = event.get("result")
        if isinstance(result, dict) and result.get("ok") is False:
            findings.append({"severity": "warning", "code": "shared_capability_failed",
                             "message": f"Capability {event.get('capability')!r} returned ok=false."})

    if paired and not connected:
        findings.append({"severity": "warning", "code": "paired_not_connected",
                         "message": "Pairing completed in this run but no connected event followed."})

    candidates_out = []
    for stable_id, candidate in sorted(candidate_records.items()):
        endpoints = candidate.get("endpoint_candidates")
        endpoint_out: list[dict[str, Any]] = []
        if isinstance(endpoints, list):
            for endpoint in endpoints:
                if isinstance(endpoint, dict):
                    endpoint_out.append({
                        "address": endpoint.get("address"),
                        "port": endpoint.get("port"),
                        "source": endpoint.get("source"),
                        "priority": endpoint.get("priority"),
                        "validation": endpoint.get("validation"),
                    })
        candidates_out.append({
            "stable_id": _stable_short(stable_id, full_ids),
            "hostname": candidate.get("hostname"),
            "platform": candidate.get("platform"),
            "app_version": candidate.get("app_version"),
            "protocol_version": candidate.get("protocol_version"),
            "compatible": candidate.get("compatible"),
            "connectable": candidate.get("connectable"),
            "transport_generation": candidate.get("transport_generation"),
            "identity_fingerprint": _stable_short(candidate.get("identity_fingerprint"), full_ids),
            "transport_fingerprint": _stable_short(candidate.get("transport_fingerprint"), full_ids),
            "endpoints": endpoint_out,
        })

    return {
        "run": run_no,
        "event_count": len(events),
        "event_counts": dict(sorted(event_counts.items())),
        "start": None if started is None else {
            "version": started.get("version"),
            "role": started.get("role"),
            "state": started.get("state"),
            "node_id": _stable_short(started.get("node_id"), full_ids),
            "bound_host": started.get("bound_host"),
            "bound_port": started.get("bound_port"),
            "discovery_started": started.get("discovery_started"),
            "discovery_disabled": started.get("discovery_disabled"),
            "tls_fingerprint": _stable_short(started.get("tls_fingerprint"), full_ids),
        },
        "candidate_observations": candidate_observations,
        "candidates": candidates_out,
        "paired": [
            {
                "peer_id": _stable_short(e.get("peer_id"), full_ids),
                "permissions": sorted(e.get("permissions", []))
                if isinstance(e.get("permissions"), list) else e.get("permissions"),
            }
            for e in paired
        ],
        "routes": _route_stats(events),
        "connections": [
            {
                "peer_id": _stable_short(e.get("peer_id"), full_ids),
                "generation": e.get("generation"),
                "tls_verified": e.get("tls_verified"),
            }
            for e in connected
        ],
        "shared_capabilities": [
            {
                "capability": e.get("capability"),
                "ok": e.get("result", {}).get("ok")
                if isinstance(e.get("result"), dict) else None,
            }
            for e in shared
        ],
        "findings": findings,
    }


def _continuity_findings(
    events: list[dict[str, Any]], *, full_ids: bool
) -> list[dict[str, str]]:
    findings: list[dict[str, str]] = []
    starts = [e for e in events if e.get("event") == "started"]

    local_ids = {str(e["node_id"]) for e in starts if e.get("node_id") is not None}
    if len(local_ids) > 1:
        findings.append({
            "severity": "warning",
            "code": "local_node_id_changed",
            "message": "Local NodeId changed across runs: "
                       + ", ".join(sorted(_stable_short(x, full_ids) or "" for x in local_ids)),
        })

    versions = [str(e["version"]) for e in starts if e.get("version") is not None]
    if len(set(versions)) > 1:
        findings.append({
            "severity": "info",
            "code": "multiple_versions",
            "message": "Log contains multiple app versions: " + ", ".join(sorted(set(versions))),
        })

    peer_records: dict[str, list[dict[str, Any]]] = defaultdict(list)
    peer_order: list[str] = []
    for event in events:
        candidate = _candidate_from_event(event)
        if candidate is None or candidate.get("stable_id") is None:
            continue
        sid = str(candidate["stable_id"])
        peer_records[sid].append(candidate)
        if not peer_order or peer_order[-1] != sid:
            peer_order.append(sid)

    if len(peer_records) > 1:
        findings.append({
            "severity": "info",
            "code": "multiple_peer_identities",
            "message": (
                "Different peer stable IDs appear across the log: "
                + ", ".join(sorted(_stable_short(x, full_ids) or "" for x in peer_records))
            ),
        })

    for sid, records in sorted(peer_records.items()):
        roots = {str(r["root_public_key"]) for r in records if r.get("root_public_key") is not None}
        identities = {
            str(r["identity_fingerprint"])
            for r in records
            if r.get("identity_fingerprint") is not None
        }
        if len(roots) > 1 or len(identities) > 1:
            findings.append({
                "severity": "error",
                "code": "stable_id_identity_conflict",
                "message": (
                    f"Peer {_stable_short(sid, full_ids)} was observed with conflicting "
                    "root/identity material."
                ),
            })

        by_generation: dict[int, set[str]] = defaultdict(set)
        sequence: list[int] = []
        for record in records:
            generation = record.get("transport_generation")
            fingerprint = record.get("transport_fingerprint")
            if isinstance(generation, int) and not isinstance(generation, bool):
                sequence.append(generation)
                if fingerprint is not None:
                    by_generation[generation].add(str(fingerprint))
        for generation, fingerprints in sorted(by_generation.items()):
            if len(fingerprints) > 1:
                findings.append({
                    "severity": "error",
                    "code": "transport_generation_conflict",
                    "message": (
                        f"Peer {_stable_short(sid, full_ids)} generation {generation} "
                        "was observed with multiple TLS fingerprints."
                    ),
                })
        if any(b < a for a, b in zip(sequence, sequence[1:])):
            findings.append({
                "severity": "warning",
                "code": "transport_generation_regressed",
                "message": (
                    f"Peer {_stable_short(sid, full_ids)} transport generation moved backwards."
                ),
            })

    return findings


def analyze_file(path: Path, *, full_ids: bool = False) -> dict[str, Any]:
    events = _load_events(path)
    runs = _split_runs(events)
    return {
        "file": path.name,
        "event_count": len(events),
        "run_count": len(runs),
        "event_counts": dict(sorted(Counter(
            str(e.get("event") or "<missing>") for e in events
        ).items())),
        "runs": [
            _run_report(i + 1, run, full_ids=full_ids)
            for i, run in enumerate(runs)
        ],
        "continuity_findings": _continuity_findings(events, full_ids=full_ids),
    }


def _collect_paths(inputs: list[str]) -> list[Path]:
    paths: set[Path] = set()
    for raw in inputs:
        path = Path(raw)
        if path.is_file():
            paths.add(path.resolve())
        elif path.is_dir():
            for candidate in path.rglob("*.json"):
                if candidate.is_file():
                    paths.add(candidate.resolve())
        else:
            raise ValueError(f"input does not exist: {path}")
    return sorted(paths, key=lambda p: str(p))


def analyze(inputs: list[str], *, full_ids: bool = False) -> dict[str, Any]:
    paths = _collect_paths(inputs)
    if not paths:
        raise ValueError("no JSON files found")

    files = [analyze_file(path, full_ids=full_ids) for path in paths]

    versions: set[str] = set()
    local_nodes: set[str] = set()
    total_events = 0
    finding_counts = Counter()
    for report in files:
        total_events += report["event_count"]
        for run in report["runs"]:
            start = run.get("start")
            if isinstance(start, dict):
                if start.get("version") is not None:
                    versions.add(str(start["version"]))
                if start.get("node_id") is not None:
                    local_nodes.add(str(start["node_id"]))
            for finding in run["findings"]:
                finding_counts[finding["severity"]] += 1
        for finding in report["continuity_findings"]:
            finding_counts[finding["severity"]] += 1

    return {
        "format": "expra-connect-log-analysis/v1",
        "summary": {
            "files": len(files),
            "total_events": total_events,
            "versions": sorted(versions),
            "local_node_ids": sorted(local_nodes),
            "findings": dict(sorted(finding_counts.items())),
        },
        "files": files,
    }


def _print_text(report: dict[str, Any]) -> None:
    summary = report["summary"]
    print("EXPRA CONNECT LOG ANALYSIS")
    print("=" * 26)
    print(f"Files: {summary['files']}")
    print(f"Events: {summary['total_events']}")
    print("Versions: " + (", ".join(summary["versions"]) or "none"))
    print("Local nodes: " + (", ".join(summary["local_node_ids"]) or "none"))
    finding_bits = [f"{k}={v}" for k, v in summary["findings"].items()]
    print("Findings: " + (", ".join(finding_bits) or "none"))

    for file_report in report["files"]:
        print()
        print(f"FILE: {file_report['file']}")
        print(f"  Events: {file_report['event_count']} | Runs: {file_report['run_count']}")
        for run in file_report["runs"]:
            start = run["start"] or {}
            print()
            print(
                f"  Run {run['run']}: version={start.get('version')} "
                f"role={start.get('role')} node={start.get('node_id')}"
            )
            print(
                f"    listener={start.get('bound_host')}:{start.get('bound_port')} "
                f"discovery={start.get('discovery_started')}"
            )
            print(
                f"    candidates={len(run['candidates'])} "
                f"paired={len(run['paired'])} "
                f"connections={len(run['connections'])} "
                f"shared_results={len(run['shared_capabilities'])}"
            )

            for candidate in run["candidates"]:
                routes = ", ".join(
                    f"{x.get('source')}:{x.get('address')}:{x.get('port')}"
                    for x in candidate["endpoints"]
                ) or "none"
                print(
                    f"    peer={candidate['stable_id']} platform={candidate['platform']} "
                    f"version={candidate['app_version']} gen={candidate['transport_generation']} "
                    f"routes=[{routes}]"
                )

            for phase, stats in run["routes"].items():
                timing = stats["successful_duration_ms"]
                timing_text = (
                    f"mean={timing['mean']}ms min={timing['min']}ms max={timing['max']}ms"
                    if timing else "no durations"
                )
                print(
                    f"    route/{phase}: {stats['outcomes']} {timing_text}"
                )

            for conn in run["connections"]:
                print(
                    f"    connected peer={conn['peer_id']} gen={conn['generation']} "
                    f"tls_verified={conn['tls_verified']}"
                )

            for shared in run["shared_capabilities"]:
                print(
                    f"    capability={shared['capability']} ok={shared['ok']}"
                )

            for finding in run["findings"]:
                print(
                    f"    [{finding['severity'].upper()}] "
                    f"{finding['code']}: {finding['message']}"
                )

        for finding in file_report["continuity_findings"]:
            print(
                f"  [{finding['severity'].upper()}] "
                f"{finding['code']}: {finding['message']}"
            )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Deterministically analyze Expra Connect JSON event logs."
    )
    parser.add_argument("inputs", nargs="+", help="JSON files and/or directories")
    parser.add_argument("--json", action="store_true", help="emit machine-readable JSON")
    parser.add_argument(
        "--full-identifiers",
        action="store_true",
        help="show full public IDs/fingerprints instead of shortened forms",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="exit 2 if warning/error findings are present",
    )
    args = parser.parse_args(argv)

    try:
        report = analyze(args.inputs, full_ids=args.full_identifiers)
    except (OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True, ensure_ascii=False))
    else:
        _print_text(report)

    if args.strict:
        findings = report["summary"]["findings"]
        if findings.get("warning", 0) or findings.get("error", 0):
            return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Two-node acceptance harness for Expra Connect.

This is deliberately a host-level test runner, not part of the package API.
It prints redacted JSON events and writes the same report to disk.
"""

from __future__ import annotations

import argparse
import json
import time
from dataclasses import asdict
from enum import Enum
from pathlib import Path
from typing import Any

from expra_connect import ConnectConfig, ConnectRuntime, NodeId

CAPABILITY = "test.read_state"


def json_default(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    return str(value)


def write_event(report_path: Path, event: str, **values: Any) -> None:
    record = {"event": event, **values}
    print(json.dumps(record, sort_keys=True, default=json_default), flush=True)
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
        json.dumps(history, indent=2, sort_keys=True, default=json_default) + "\n",
        encoding="utf-8",
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", type=Path, required=True)
    parser.add_argument("--role", choices=("target", "initiator"), required=True)
    parser.add_argument("--peer-id", help="target peer node ID for initiator mode")
    parser.add_argument("--wait", type=float, default=60.0)
    parser.add_argument("--report", type=Path, default=Path("peer-report.json"))
    args = parser.parse_args()

    runtime: ConnectRuntime

    def approve_pairing(request: Any) -> bool:
        write_event(
            args.report,
            "pairing_request",
            caller_node_id=request.caller_node_id.value,
            permissions=sorted(permission.value for permission in request.permissions),
        )
        try:
            runtime.sharing.register(
                CAPABILITY,
                lambda peer_id, params: {
                    "ok": True,
                    "peer_id": peer_id.value,
                    "params": params,
                },
            )
        except ValueError:
            pass
        runtime.sharing.allow(request.caller_node_id, CAPABILITY)
        return True

    runtime = ConnectRuntime(
        ConnectConfig(
            profile_dir=args.profile,
            on_pairing_request=approve_pairing if args.role == "target" else None,
            discovery_enabled=True,
        )
    )
    status = runtime.start()
    identity = runtime.identity
    if identity is None:
        raise RuntimeError("runtime did not create an identity")
    write_event(
        args.report,
        "started",
        role=args.role,
        node_id=identity.node_id.value,
        state=status.state.value,
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
            while True:
                time.sleep(1)

        deadline = time.monotonic() + args.wait
        candidate = None
        while time.monotonic() < deadline:
            if args.peer_id:
                candidate = next(
                    (peer for peer in runtime.peers if peer.stable_id == args.peer_id),
                    None,
                )
            elif runtime.peers:
                candidate = runtime.peers[0]
            if candidate is not None:
                break
            time.sleep(1)
        if candidate is None:
            write_event(
                args.report,
                "discovery_timeout",
                peers=[asdict(peer) for peer in runtime.peers],
            )
            return 2

        write_event(args.report, "discovered", candidate=asdict(candidate))
        peer_id = NodeId(candidate.stable_id)
        trusted = runtime.pair_peer(peer_id)
        write_event(
            args.report,
            "paired",
            peer_id=trusted.peer_id.value,
            permissions=sorted(trusted.permissions),
        )
        provider = runtime.connect_peer(peer_id)
        write_event(args.report, "connected", peer_id=peer_id.value)
        result = provider.request_shared(CAPABILITY, {"source": "run_peer.py"})
        write_event(args.report, "shared_capability_result", result=result)
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

"""Detailed Linux target for the Windows pairing acceptance test.

This version exposes the host-side responsibilities that a real application
must implement around Expra Connect: policy, reporting, capability registration,
and lifecycle management.
"""

from __future__ import annotations

import argparse
import json
import time
from dataclasses import asdict, is_dataclass
from pathlib import Path
from typing import Any

from expra_connect import ConnectConfig, ConnectRuntime, RuntimeState, __version__

CAPABILITY = "test.read_state"


class EventLog:
    """Print redacted events and persist them as a JSON acceptance report."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.events: list[dict[str, Any]] = []

    def write(self, event: str, **values: Any) -> None:
        # Reports contain operational evidence only. Private keys, pairing
        # secrets, and token values must never cross this boundary.
        record = {"event": event, **values}
        self.events.append(record)
        print(json.dumps(record, sort_keys=True, default=str), flush=True)
        self.path.write_text(
            json.dumps(self.events, indent=2, sort_keys=True, default=str) + "\n",
            encoding="utf-8",
        )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", type=Path, default=Path(".expra-windows-target"))
    parser.add_argument("--report", type=Path, default=Path("linux-target.json"))
    args = parser.parse_args()
    report = EventLog(args.report)

    runtime: ConnectRuntime

    def approve_pairing(request: Any) -> bool:
        # Discovery is not authorization. The host explicitly approves only
        # the read-only request and then grants the target-owned capability.
        report.write(
            "pairing_request",
            caller_node_id=request.caller_node_id.value,
            permissions=sorted(permission.value for permission in request.permissions),
        )
        runtime.sharing.allow(request.caller_node_id, CAPABILITY)
        return True

    def on_discovery(kind: str, payload: Any) -> None:
        # Discovery callbacks are observations. They do not implicitly pair,
        # trust, or connect a peer.
        report.write(
            "discovery_event",
            kind=kind,
            payload=asdict(payload) if is_dataclass(payload) else payload,
        )

    runtime = ConnectRuntime(
        ConnectConfig(
            profile_dir=args.profile,
            on_pairing_request=approve_pairing,
            on_discovery=on_discovery,
            discovery_enabled=True,
        )
    )
    # Register before start so a fast initiator cannot race capability setup.
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
    if identity is None or status.state is not RuntimeState.STARTED:
        report.write("startup_failed", status=asdict(status))
        return 1

    report.write(
        "ready",
        version=__version__,
        node_id=identity.node_id.value,
        status=asdict(status),
        diagnostics=runtime.diagnostics(),
    )
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        return 0
    finally:
        report.write("stopping")
        runtime.shutdown()


if __name__ == "__main__":
    raise SystemExit(main())

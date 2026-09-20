"""Run a Linux Expra Connect target for the Windows pairing acceptance test."""

from __future__ import annotations

import argparse
import json
import time
from dataclasses import asdict
from pathlib import Path
from typing import Any

from expra_connect import ConnectConfig, ConnectRuntime, RuntimeState, __version__

CAPABILITY = "test.read_state"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", type=Path, default=Path(".expra-windows-target"))
    args = parser.parse_args()

    runtime: ConnectRuntime

    def approve_pairing(request: Any) -> bool:
        print(
            json.dumps(
                {
                    "event": "pairing_request",
                    "caller_node_id": request.caller_node_id.value,
                    "permissions": sorted(
                        permission.value for permission in request.permissions
                    ),
                },
                sort_keys=True,
            ),
            flush=True,
        )
        runtime.sharing.allow(request.caller_node_id, CAPABILITY)
        return True

    runtime = ConnectRuntime(
        ConnectConfig(
            profile_dir=args.profile,
            on_pairing_request=approve_pairing,
            discovery_enabled=True,
        )
    )
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
        print(json.dumps({"status": asdict(status)}, sort_keys=True), flush=True)
        return 1

    print(
        json.dumps(
            {
                "event": "ready",
                "version": __version__,
                "node_id": identity.node_id.value,
                "status": asdict(status),
            },
            sort_keys=True,
        ),
        flush=True,
    )
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        return 0
    finally:
        runtime.shutdown()


if __name__ == "__main__":
    raise SystemExit(main())

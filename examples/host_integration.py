"""Minimal host lifecycle example; networking requires an explicit opt-in."""

from __future__ import annotations

import argparse
import queue
from pathlib import Path
from threading import Event
from typing import Any

from expra_connect import ConnectConfig, ConnectRuntime, RuntimeState


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", type=Path, default=Path("./connect-profile"))
    parser.add_argument(
        "--serve", action="store_true", help="start the peer listener and discovery"
    )
    args = parser.parse_args()
    if not args.serve:
        print("No runtime started. Pass --serve to opt in to networking.")
        return 0

    events: queue.Queue[tuple[str, Any]] = queue.Queue()
    stopping = Event()

    def on_discovery(kind: str, payload: Any) -> None:
        # Runtime callbacks may run off the host/UI thread: hand off immutable
        # event data and let the application's event loop decide what to do.
        events.put((kind, payload))

    def on_pairing_request(_request: object) -> bool:
        # Pairing approval is synchronous. This example denies by default;
        # host UI handoffs must not block a thread needed by the runtime.
        return False

    runtime = ConnectRuntime(
        ConnectConfig(
            profile_dir=args.profile,
            on_discovery=on_discovery,
            on_pairing_request=on_pairing_request,
        )
    )
    try:
        status = runtime.start()
        if status.state is not RuntimeState.STARTED:
            raise RuntimeError(f"Connect runtime did not start: {status}")
        print(f"Runtime started on {status.bound_host}:{status.bound_port}")
        try:
            while not stopping.is_set():
                try:
                    kind, payload = events.get(timeout=0.25)
                except queue.Empty:
                    continue
                print(f"Discovery event: {kind} ({type(payload).__name__})")
        except KeyboardInterrupt:
            pass
    finally:
        runtime.shutdown()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

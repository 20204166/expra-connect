"""Deterministic harness surfaces and the surface synchronization barrier."""

from __future__ import annotations

import time
from pathlib import Path
from threading import Event
from typing import Any

from expra_connect import ConnectRuntime
from expra_connect.wire_protocol import RemoteAuthorizationError

from .events import write_event

SURFACE_SYNC_CAPABILITY = "test.surface_sync"
SURFACE_SYNC_TIMEOUT = 5.0


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

        def read(
            _peer_id: Any, _params: dict[str, Any], result: Any = read_result
        ) -> dict[str, Any]:
            return dict(result)

        def review(
            _peer_id: Any, _params: dict[str, Any], result: Any = review_result
        ) -> dict[str, Any]:
            return dict(result)

        def save(
            _peer_id: Any, _params: dict[str, Any], name: str = surface_id
        ) -> dict[str, Any]:
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


def register_surface_sync(
    runtime: ConnectRuntime, ready_events: dict[str, Event]
) -> None:
    """Register a private harness barrier without granting surface access."""

    def wait_for_surface(_peer_id: Any, params: dict[str, Any]) -> dict[str, bool]:
        sync_key = params.get("sync_key")
        event = ready_events.get(sync_key) if isinstance(sync_key, str) else None
        return {"ready": event.wait(SURFACE_SYNC_TIMEOUT) if event else False}

    runtime.sharing.register(
        SURFACE_SYNC_CAPABILITY,
        wait_for_surface,
    )


def _retry_surface_success(operation: Any, timeout: float = 5.0) -> Any:
    """Allow a target host to publish a matching ephemeral grant."""
    deadline = time.monotonic() + max(timeout, 0.0)
    while True:
        try:
            return operation()
        except RemoteAuthorizationError:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise
            time.sleep(min(0.1, remaining))


def approve_surface_pairing(
    runtime: ConnectRuntime, report: Path, request: Any
) -> bool:
    """Approve the trust handshake without implicitly granting surface access."""
    permissions = sorted(
        getattr(permission, "value", permission)
        for permission in getattr(request, "permissions", ())
    )
    write_event(report, "pairing_request", permissions=permissions)
    runtime.sharing.allow(request.caller_node_id, SURFACE_SYNC_CAPABILITY)
    return True

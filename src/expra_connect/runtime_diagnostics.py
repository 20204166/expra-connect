"""Secret-free operational diagnostics for the runtime composition root."""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from .identity import node_identity_fingerprint


def build_diagnostics(runtime: Any) -> dict[str, Any]:
    """Build operational metadata without credentials or invitation material."""

    identity = runtime._identity
    generations = runtime._transport_generations
    result: dict[str, Any] = {
        "status": asdict(runtime._status),
        "generation": runtime._generation,
        "identity": None,
        "device_identity": runtime._device_identity_diagnostics(),
        "transport": {
            "fingerprint": runtime._status.tls_fingerprint,
            "current_generation": (
                generations.current_generation if generations is not None else None
            ),
            "pending_generation": (
                generations.next.generation
                if generations is not None and generations.next is not None
                else None
            ),
        },
        "routes": [],
        "connections": [],
        "sessions": [],
        "observability": asdict(runtime._observer.snapshot()),
    }
    if identity is not None:
        result["identity"] = {
            "node_id": identity.node_id.value,
            "root_fingerprint": node_identity_fingerprint(identity.root_public_key),
        }
    for candidate in runtime.peers:
        result["routes"].append(
            {
                "node_id": candidate.stable_id,
                "hostname": candidate.hostname,
                "addresses": candidate.addresses,
                "port": candidate.port,
                "transport_fingerprint": candidate.transport_fingerprint,
                "last_seen": candidate.last_seen,
            }
        )
    if runtime._registry is not None:
        for record in runtime._registry.records:
            result["connections"].append(
                {
                    "node_id": record.node_id.value,
                    "state": record.connection.status.value,
                    "reason": record.connection.reason,
                    "changed_at": record.connection.changed_at,
                }
            )
    if runtime._connection_manager is not None:
        result["sessions"] = [
            node_id.value for node_id in runtime._connection_manager.providers
        ]
    return result

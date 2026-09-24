"""Secret-free operational diagnostics for the runtime composition root."""

from __future__ import annotations

import base64
from dataclasses import asdict
from hashlib import sha256
from typing import Any

from .connection_manager import ConnectionManager
from .identity import NodeId, node_identity_fingerprint
from .pairing import PairingManager, TrustedPeer


def build_peer_diagnostics(runtime: Any, peer_id: NodeId) -> dict[str, Any]:
    """Build deterministic, secret-free directional pairing evidence."""

    pairing = runtime._require_pairing()
    trusted = pairing.trusted.get(peer_id)
    grant = pairing.grants.get(peer_id)
    pending = pairing.find_pending_for(peer_id)
    candidate = runtime._peers.get(peer_id.value)
    identity: dict[str, Any] | None = None
    if trusted is not None or candidate is not None:
        identity = {
            "known_root_fingerprint": _root_fingerprint(
                (trusted.root_public_key if trusted else None)
                or (candidate.root_public_key if candidate else None)
            ),
            "candidate_matches_stored_root": _candidate_root_matches(
                trusted, candidate
            ),
            "transport_generation": (
                candidate.transport_generation
                if candidate is not None
                else (trusted.transport_generation if trusted else None)
            ),
            "transport_continuity": _transport_continuity(trusted, candidate),
        }
    manager = runtime._connection_manager
    connected = manager is not None and peer_id in manager.providers
    return {
        "node_id": runtime._identity.node_id.value if runtime._identity else None,
        "peer_id": peer_id.value,
        "relationship": pairing.relationship(peer_id).value,
        "outbound": {
            "trusted": trusted is not None,
            "auth_broken": pairing.is_auth_broken(peer_id),
            "transport_generation": (
                trusted.transport_generation if trusted is not None else None
            ),
        },
        "inbound": {"granted": grant is not None},
        "pending": {
            "intent": pending.intent if pending is not None else None,
            "direction": pending.direction if pending is not None else None,
            "count": _pending_count(pairing, peer_id),
            "expires_at": pending.expires_at if pending is not None else None,
        },
        "identity": identity,
        "connection": {"connected": connected},
        "constants": {
            "outbound_trust_calls": "this node -> peer",
            "inbound_grant_authorizes": "peer -> this node",
        },
    }


def _root_fingerprint(root_public_key: str | None) -> str | None:
    if not root_public_key:
        return None
    try:
        digest = sha256(base64.b64decode(root_public_key, validate=True)).hexdigest()
    except (ValueError, TypeError):
        return None
    return "sha256:" + digest


def _candidate_root_matches(trusted: TrustedPeer | None, candidate: Any) -> bool | None:
    if trusted is None or candidate is None:
        return None
    if not trusted.root_public_key or not candidate.root_public_key:
        return None
    return bool(trusted.root_public_key == candidate.root_public_key)


def _transport_continuity(trusted: TrustedPeer | None, candidate: Any) -> str:
    if trusted is None:
        return "no_stored_trust"
    if candidate is None:
        return "no_candidate"
    if candidate.transport_fingerprint == trusted.transport_fingerprint:
        return "same_generation"
    if ConnectionManager._candidate_generation_allowed(trusted, candidate):
        return "rotation_proven"
    return "unproven"


def _pending_count(pairing: PairingManager, peer_id: NodeId) -> int:
    return sum(1 for item in pairing.pending.values() if item.peer_id == peer_id)


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

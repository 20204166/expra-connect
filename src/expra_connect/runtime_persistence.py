"""Serialization helpers for runtime-owned trust and cluster state."""

from __future__ import annotations

import math
from typing import Any

from .identity import NodeId
from .models import NodePermission
from .pairing import PeerGrant, PendingPairing, TrustedPeer


def peer_grant_to_json(grant: PeerGrant) -> dict[str, Any]:
    return {
        "caller_id": grant.caller_id.value,
        "secret": grant.secret,
        "permissions": sorted(grant.permissions),
        "identity_fingerprint": grant.identity_fingerprint,
        "transport_fingerprint": grant.transport_fingerprint,
        "root_public_key": grant.root_public_key,
        "transport_generation": grant.transport_generation,
        "transport_proof": grant.transport_proof,
    }


def peer_grant_from_json(value: Any) -> PeerGrant:
    if not isinstance(value, dict):
        raise TypeError("persisted grant must be an object")
    permissions = permissions_from_json(value.get("permissions"))
    validate_secret(value.get("secret"))
    return PeerGrant(
        caller_id=NodeId(str(value["caller_id"])),
        secret=str(value["secret"]),
        permissions=permissions,
        identity_fingerprint=optional_text(value.get("identity_fingerprint")),
        transport_fingerprint=optional_text(value.get("transport_fingerprint")),
        root_public_key=optional_text(value.get("root_public_key")),
        transport_generation=optional_generation(value.get("transport_generation")),
        transport_proof=optional_text(value.get("transport_proof")),
    )


def trusted_peer_to_json(peer: TrustedPeer) -> dict[str, Any]:
    return {
        "peer_id": peer.peer_id.value,
        "secret": peer.secret,
        "permissions": sorted(peer.permissions),
        "identity_fingerprint": peer.identity_fingerprint,
        "transport_fingerprint": peer.transport_fingerprint,
        "root_public_key": peer.root_public_key,
        "transport_generation": peer.transport_generation,
        "transport_proof": peer.transport_proof,
    }


def trusted_peer_from_json(value: Any) -> TrustedPeer:
    if not isinstance(value, dict):
        raise TypeError("persisted trusted peer must be an object")
    permissions = permissions_from_json(value.get("permissions"))
    validate_secret(value.get("secret"))
    return TrustedPeer(
        peer_id=NodeId(str(value["peer_id"])),
        secret=str(value["secret"]),
        permissions=permissions,
        identity_fingerprint=optional_text(value.get("identity_fingerprint")),
        transport_fingerprint=optional_text(value.get("transport_fingerprint")),
        root_public_key=optional_text(value.get("root_public_key")),
        transport_generation=optional_generation(value.get("transport_generation")),
        transport_proof=optional_text(value.get("transport_proof")),
    )


def pending_pairing_to_json(
    pending: PendingPairing, permissions: frozenset[str]
) -> dict[str, Any]:
    return {
        "transaction_id": pending.transaction_id,
        "peer_id": pending.peer_id.value,
        "secret": pending.secret,
        "expires_at": pending.expires_at,
        "identity_fingerprint": pending.identity_fingerprint,
        "transport_fingerprint": pending.transport_fingerprint,
        "root_public_key": pending.root_public_key,
        "transport_generation": pending.transport_generation,
        "transport_proof": pending.transport_proof,
        "direction": pending.direction,
        "intent": pending.intent,
        "permissions": sorted(permissions),
    }


def pending_pairing_from_json(
    value: Any,
) -> tuple[PendingPairing, frozenset[str]]:
    if not isinstance(value, dict):
        raise TypeError("persisted pending pairing must be an object")
    expires_at = value["expires_at"]
    if (
        isinstance(expires_at, bool)
        or not isinstance(expires_at, (int, float))
        or not math.isfinite(float(expires_at))
    ):
        raise TypeError("persisted pending pairing expiry is invalid")
    validate_secret(value.get("secret"))
    permissions = value.get("permissions", [])
    if not isinstance(permissions, list) or any(
        not isinstance(item, str) for item in permissions
    ):
        raise TypeError("persisted pending pairing permissions are invalid")
    direction = value.get("direction", "outbound")
    if direction not in {"outbound", "inbound"}:
        raise TypeError("persisted pending pairing direction is invalid")
    intent = value.get("intent", "pair")
    if intent not in {"pair", "repair"}:
        raise TypeError("persisted pending pairing intent is invalid")
    return (
        PendingPairing(
            transaction_id=str(value["transaction_id"]),
            peer_id=NodeId(str(value["peer_id"])),
            secret=str(value["secret"]),
            expires_at=float(expires_at),
            identity_fingerprint=optional_text(value.get("identity_fingerprint")),
            transport_fingerprint=optional_text(value.get("transport_fingerprint")),
            root_public_key=optional_text(value.get("root_public_key")),
            transport_generation=optional_generation(value.get("transport_generation")),
            transport_proof=optional_text(value.get("transport_proof")),
            direction=direction,
            intent=intent,
        ),
        permissions_from_json(permissions),
    )


def optional_text(value: Any) -> str | None:
    return None if value is None else str(value)


def optional_generation(value: Any) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise TypeError("persisted transport generation is invalid")
    return value


def permissions_from_json(value: Any) -> frozenset[str]:
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise TypeError("persisted permissions are invalid")
    known = {permission.value for permission in NodePermission}
    permissions = frozenset(value)
    if not permissions <= known:
        raise ValueError("persisted permissions contain an unknown value")
    return permissions


def validate_secret(value: Any) -> None:
    if not isinstance(value, str) or len(value) != 64:
        raise ValueError("persisted peer secret is invalid")
    try:
        decoded = bytes.fromhex(value)
    except ValueError as error:
        raise ValueError("persisted peer secret is invalid") from error
    # ``bytes.fromhex`` permits whitespace, so length-check the decoded key too.
    if len(decoded) != 32:
        raise ValueError("persisted peer secret is invalid")

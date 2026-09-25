"""Target-side classification of inbound transactional pairing requests.

The runtime remains the composition root; this module owns the security
ordering that decides whether an inbound ``pair_request`` becomes a fresh pair,
a structured ``already_paired`` refusal, an explicit repair, or a fail-closed
identity conflict. The stored inbound grant is authoritative and the caller
must prove possession of that root before any repair is considered.
"""

from __future__ import annotations

from typing import Any

from .pairing import PairingBusy, PendingPairing, grant_root_continuous
from .wire_protocol import PairingRequest


def handle_pairing_request(runtime: Any, request: PairingRequest) -> dict[str, Any]:
    pairing = runtime._require_pairing()
    caller = request.caller_node_id
    if caller == runtime.identity.node_id:
        return {"approved": False, "outcome": "denied"}
    existing_grant = pairing.grants.get(caller)
    if existing_grant is not None:
        if not grant_root_continuous(
            existing_grant,
            root_public_key=request.root_public_key,
            transport_generation=request.transport_generation,
            transport_fingerprint=request.transport_fingerprint,
            transport_proof=request.transport_proof,
        ):
            return {"approved": False, "outcome": "identity_conflict"}
        if request.intent != "repair":
            return {"approved": False, "outcome": "already_paired"}
    duplicate = pairing.find_pending_for(caller, direction="inbound")
    if duplicate is not None:
        if duplicate.secret == request.proposed_secret:
            return pairing_response(runtime, duplicate)
        return {"approved": False, "outcome": "already_pending"}
    callback = runtime.config.on_pairing_request
    try:
        approved = callback is not None and callback(request) is True
    except Exception:  # noqa: BLE001 - a broken approval callback must fail closed.
        approved = False
    if not approved:
        return {"approved": False, "outcome": "denied"}
    try:
        pending = pairing.begin(
            caller,
            secret=request.proposed_secret,
            identity_fingerprint=request.identity_fingerprint,
            transport_fingerprint=request.transport_fingerprint,
            root_public_key=request.root_public_key,
            transport_generation=request.transport_generation,
            transport_proof=request.transport_proof,
            direction="inbound",
            intent=request.intent,
        )
    except PairingBusy:
        return {"approved": False, "outcome": "already_pending"}
    runtime._pending_permissions[pending.transaction_id] = frozenset(
        permission.value for permission in request.permissions
    )
    if not runtime._save_persisted_state():
        pairing.abort(pending.transaction_id)
        runtime._pending_permissions.pop(pending.transaction_id, None)
        return {"approved": False, "outcome": "persistence_failed"}
    return pairing_response(runtime, pending)


def pairing_response(runtime: Any, pending: PendingPairing) -> dict[str, Any]:
    return {
        "approved": True,
        "transaction_id": pending.transaction_id,
        "caller_node_id": pending.peer_id.value,
        "identity_fingerprint": pending.identity_fingerprint,
        "transport_fingerprint": pending.transport_fingerprint,
        "root_public_key": pending.root_public_key,
        "transport_generation": pending.transport_generation,
        "transport_proof": pending.transport_proof,
        "intent": pending.intent,
        "secret": pending.secret,
        "permissions": sorted(runtime._pending_permissions[pending.transaction_id]),
        "expires_at": pending.expires_at,
    }

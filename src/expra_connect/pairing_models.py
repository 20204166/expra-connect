"""Small pairing transport value objects kept outside the service facade."""

from dataclasses import dataclass
from typing import Any

from .models import NodePermission


@dataclass(frozen=True, slots=True)
class PairingTransaction:
    """Target approval binding retained until local trust is confirmed."""

    transaction_id: str
    caller_node_id: str
    identity_fingerprint: str
    transport_fingerprint: str
    secret: str
    permissions: frozenset[NodePermission]
    expires_at: float
    transport: Any
    root_public_key: str | None = None
    transport_generation: int | None = None
    transport_proof: str | None = None

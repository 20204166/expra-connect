"""Connection credentials projected into the socket transport."""

from __future__ import annotations

from dataclasses import dataclass

from ..data.permissions import READ_PERMISSIONS, NodePermission
from ..identity import NodeId


@dataclass(frozen=True, slots=True)
class TrustedNodeRecord:
    node_id: NodeId
    host: str
    port: int
    secret: str
    transport_fingerprint: str
    permissions: frozenset[NodePermission] = READ_PERMISSIONS
    root_public_key: str | None = None
    transport_generation: int | None = None
    transport_proof: str | None = None

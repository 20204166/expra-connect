"""Neutral wire-domain values adapted from the peer core's public model."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from .identity import NodeId


class NodeCapability(str, Enum):
    READ_STATE = "read_state"
    REMOTE_MANAGEMENT = "remote_management"


class NodePermission(str, Enum):
    READ_STATE = "read_state"
    REMOTE_MANAGEMENT = "remote_management"
    PROCESS_TERMINATION = "process_termination"
    PROCESS_FORCE_TERMINATION = "process_force_termination"
    CLEANUP = "cleanup"


class ProcessActionKind(str, Enum):
    REQUEST = "request"
    FORCE = "force"
    REQUEST_QUIT = "request_quit"
    FORCE_QUIT = "force_quit"


READ_CAPABILITIES = frozenset({NodeCapability.READ_STATE})
READ_PERMISSIONS = frozenset({NodePermission.READ_STATE})


@dataclass(frozen=True, slots=True)
class TrustedNodeRecord:
    node_id: NodeId
    host: str
    port: int
    secret: str
    transport_fingerprint: str
    permissions: frozenset[NodePermission] = READ_PERMISSIONS


@dataclass(frozen=True, slots=True)
class DiscoveredNodeCandidate:
    stable_id: str
    hostname: str
    addresses: tuple[str, ...]
    port: int | None
    service_name: str
    app_version: str
    protocol_version: str
    platform: str | None
    connectable: bool
    compatible: bool
    last_seen: float
    identity_fingerprint: str | None = None
    transport_fingerprint: str | None = None

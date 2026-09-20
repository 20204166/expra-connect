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


class EndpointSource(str, Enum):
    """Origin used for deterministic route selection."""

    CONFIGURED = "configured"
    IPV4 = "ipv4"
    IPV6 = "ipv6"
    VPN = "vpn"
    DISCOVERY = "discovery"


@dataclass(frozen=True, slots=True)
class EndpointCandidate:
    address: str
    port: int
    source: EndpointSource = EndpointSource.DISCOVERY
    validation: str = "validated"
    priority: int = 0
    last_success: float | None = None
    last_failure: float | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "source", EndpointSource(self.source))
        if not self.address.strip():
            raise ValueError("endpoint address must not be empty")
        if not 1 <= self.port <= 65535:
            raise ValueError("endpoint port is invalid")
        if self.validation not in {"validated", "unvalidated", "invalid"}:
            raise ValueError("endpoint validation is invalid")

    @property
    def key(self) -> tuple[str, int]:
        return self.address, self.port


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
    root_public_key: str | None = None
    transport_generation: int | None = None
    transport_proof: str | None = None


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
    endpoint_candidates: tuple[EndpointCandidate, ...] = ()
    root_public_key: str | None = None
    transport_generation: int | None = None
    transport_proof: str | None = None

    def __post_init__(self) -> None:
        if self.endpoint_candidates:
            return
        if self.port is None:
            return
        endpoints = tuple(
            EndpointCandidate(address, self.port, source=endpoint_source(address))
            for address in self.addresses
        )
        object.__setattr__(self, "endpoint_candidates", endpoints)


def endpoint_source(address: str) -> EndpointSource:
    """Infer a conservative source for legacy address-only observations."""

    import ipaddress

    try:
        parsed = ipaddress.ip_address(address)
    except ValueError:
        return EndpointSource.DISCOVERY
    if parsed.version == 6:
        return EndpointSource.IPV6
    return EndpointSource.IPV4

"""Discovery candidates and endpoint-selection values."""

from __future__ import annotations

import ipaddress
from dataclasses import dataclass
from enum import Enum


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
        if self.endpoint_candidates or self.port is None:
            return
        endpoints = tuple(
            EndpointCandidate(address, self.port, source=endpoint_source(address))
            for address in self.addresses
        )
        object.__setattr__(self, "endpoint_candidates", endpoints)


def endpoint_source(address: str) -> EndpointSource:
    """Infer a conservative source for legacy address-only observations."""

    try:
        parsed = ipaddress.ip_address(address)
    except ValueError:
        return EndpointSource.DISCOVERY
    return EndpointSource.IPV6 if parsed.version == 6 else EndpointSource.IPV4

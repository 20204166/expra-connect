"""Headless discovery candidate validation, ranking, and TTL state."""

from __future__ import annotations

import ipaddress
import math
import time
from collections.abc import Callable
from dataclasses import dataclass, replace

from .models import EndpointCandidate, EndpointSource


@dataclass(frozen=True, slots=True)
class DiscoveryCandidate:
    stable_id: str
    addresses: tuple[str, ...]
    port: int
    seen_at: float = 0.0

    @property
    def endpoint_candidates(self) -> tuple[EndpointCandidate, ...]:
        return tuple(
            EndpointCandidate(address, self.port) for address in self.addresses
        )


def normalize_port(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if int(value) != value or not 1 <= int(value) <= 65535:
        return None
    return int(value)


def validate_candidate(
    candidate: DiscoveryCandidate, *, self_id: str | None = None
) -> None:
    if not isinstance(candidate, DiscoveryCandidate):
        raise TypeError("invalid discovery candidate")
    if (
        not isinstance(candidate.stable_id, str)
        or not candidate.stable_id
        or candidate.stable_id == "local"
    ):
        raise ValueError("invalid discovery identity")
    if self_id is not None and candidate.stable_id == self_id:
        raise ValueError("self discovery is not a peer")
    if normalize_port(candidate.port) is None or not candidate.addresses:
        raise ValueError("invalid discovery endpoint")
    if any(
        not isinstance(address, str) or not address.strip()
        for address in candidate.addresses
    ):
        raise ValueError("invalid discovery endpoint")


def _address_rank(address: str) -> int:
    try:
        parsed = ipaddress.ip_address(address)
    except ValueError:
        return 5
    if parsed.is_loopback:
        return 4
    if parsed.version == 4 and parsed in ipaddress.ip_network("192.168.0.0/16"):
        return 0
    if parsed.version == 4 and parsed in ipaddress.ip_network("172.16.0.0/12"):
        return 1
    if parsed.version == 4 and parsed in ipaddress.ip_network("10.0.0.0/8"):
        return 2
    if parsed.version == 4 and parsed in ipaddress.ip_network("100.64.0.0/10"):
        return 3
    return 4


def preferred_address(addresses: tuple[str, ...]) -> str:
    if not addresses:
        raise ValueError("no route candidates")
    return min(addresses, key=_address_rank)


def endpoint_rank(endpoint: EndpointCandidate) -> tuple[int, int, int, str, int]:
    """Return a stable route rank; prior success beats source preference."""

    source_rank = {
        EndpointSource.CONFIGURED: 0,
        EndpointSource.IPV4: 1,
        EndpointSource.IPV6: 2,
        EndpointSource.VPN: 3,
        EndpointSource.DISCOVERY: 4,
    }[endpoint.source]
    success_rank = 0 if endpoint.last_success is not None else 1
    failure_rank = 1 if endpoint.last_failure is not None else 0
    return (
        success_rank,
        endpoint.priority,
        source_rank + failure_rank,
        endpoint.address,
        endpoint.port,
    )


def preferred_endpoint(
    endpoints: tuple[EndpointCandidate, ...],
) -> EndpointCandidate:
    usable = tuple(
        endpoint for endpoint in endpoints if endpoint.validation != "invalid"
    )
    if not usable:
        raise ValueError("no route candidates")
    return min(usable, key=endpoint_rank)


class DiscoveryRegistry:
    def __init__(
        self,
        *,
        clock: Callable[[], float] = time.monotonic,
        ttl: float = 4800.0,
        self_id: str | None = None,
    ) -> None:
        self._clock = clock
        self._ttl = ttl
        self._self_id = self_id
        self._items: dict[str, DiscoveryCandidate] = {}

    def add(self, candidate: DiscoveryCandidate) -> bool:
        try:
            validate_candidate(candidate, self_id=self._self_id)
        except (TypeError, ValueError):
            return False
        port = normalize_port(candidate.port)
        if port is None:
            return False
        item = replace(
            candidate,
            port=port,
            seen_at=self._clock(),
        )
        self._items[item.stable_id] = item
        return True

    def remove(self, stable_id: str) -> None:
        if not isinstance(stable_id, str):
            return
        self._items.pop(stable_id, None)

    def candidates(self) -> tuple[DiscoveryCandidate, ...]:
        return tuple(self._items.values())

    def expire(self, *, now: float | None = None) -> tuple[str, ...]:
        current = self._clock() if now is None else now
        expired = tuple(
            key
            for key, item in self._items.items()
            if current - item.seen_at > self._ttl
        )
        for key in expired:
            del self._items[key]
        return expired

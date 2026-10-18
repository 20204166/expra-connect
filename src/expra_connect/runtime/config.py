"""Configuration and status models for the runtime composition root."""

from __future__ import annotations

import platform
import socket
from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any

from .._version import __version__
from ..data.permissions import READ_CAPABILITIES, NodeCapability
from ..discovery.network import SERVICE_TYPE, DiscoveryBackend
from ..identity.device_hardware import DeviceHardwareProvider
from ..remote.wire_protocol import CapabilityElevationRequest, PairingRequest
from ..transport.server import PEER_SERVICE_DEFAULT_PORT


def _validate_port_range(name: str, port: int, *, integer_only: bool = False) -> None:
    message = f"{name} must be between 0 and 65535"
    if integer_only and (isinstance(port, bool) or not isinstance(port, int)):
        raise ValueError(message)
    if not 0 <= port <= 65535:
        raise ValueError(message)


class RuntimeState(str, Enum):
    STOPPED = "stopped"
    STARTING = "starting"
    STARTED = "started"
    PERSISTENCE_FAILED = "persistence_failed"
    LISTENER_UNAVAILABLE = "listener_unavailable"


@dataclass(frozen=True, slots=True)
class RuntimeStatus:
    state: RuntimeState
    listener_started: bool = False
    discovery_started: bool = False
    discovery_disabled: bool = False
    discovery_reason: str | None = None
    bound_host: str | None = None
    bound_port: int | None = None
    pairing_bound_port: int | None = None
    preferred_port_honored: bool | None = None
    tls_fingerprint: str | None = None
    reason: str | None = None


@dataclass(frozen=True, slots=True)
class ConnectConfig:
    profile_dir: Path
    app_id: str = "expra-connect"
    display_name: str = "Expra Connect Peer"
    app_version: str = __version__
    bind_host: str = "0.0.0.0"
    preferred_port: int = PEER_SERVICE_DEFAULT_PORT
    pairing_port: int = 0
    discovery_enabled: bool = True
    service_type: str = SERVICE_TYPE
    hostname: str = ""
    platform_name: str = ""
    advertised_addresses: tuple[str, ...] | None = None
    cluster_enabled: bool = False
    capabilities: frozenset[NodeCapability] = READ_CAPABILITIES
    provider: Any = None
    on_discovery: Callable[[str, Any], None] | None = None
    on_pairing_request: Callable[[PairingRequest], bool] | None = None
    on_elevation_request: Callable[[CapabilityElevationRequest], bool] | None = None
    on_route_attempt: Callable[[str, Any, str, str | None], None] | None = None
    discovery_backend_factory: (
        Callable[[Callable[[str, str, Any], None]], DiscoveryBackend] | None
    ) = None
    device_hardware_provider: DeviceHardwareProvider | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "profile_dir", Path(self.profile_dir).expanduser())
        if not self.app_id.strip():
            raise ValueError("app_id must not be empty")
        if not self.service_type.endswith("."):
            raise ValueError("service_type must be a fully qualified service type")
        _validate_port_range("preferred_port", self.preferred_port)
        _validate_port_range("pairing_port", self.pairing_port, integer_only=True)
        if self.advertised_addresses is not None and (
            not self.advertised_addresses
            or not all(address.strip() for address in self.advertised_addresses)
        ):
            raise ValueError("advertised_addresses must contain non-empty values")

    @property
    def resolved_hostname(self) -> str:
        return self.hostname or socket.gethostname()

    @property
    def resolved_platform(self) -> str:
        return self.platform_name or platform.system().lower()

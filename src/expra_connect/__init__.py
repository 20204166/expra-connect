"""Headless peer connection primitives."""

from ._version import __version__
from .cluster import Cluster, ClusterRole
from .connection_state import ConnectionState, RetryState
from .device_hardware import DeviceHardwareHint, DeviceHardwareProvider
from .device_identity import (
    DeviceIdentity,
    DeviceIdentityError,
    DeviceIdentityView,
)
from .discovery.network import NetworkDiscovery
from .failover import FailoverCoordinator
from .identity import (
    NodeId,
    NodeIdentity,
    verify_transport_proof,
)
from .models import (
    DiscoveredNodeCandidate,
    EndpointCandidate,
    EndpointSource,
    NodeCapability,
    NodePermission,
)
from .pairing.manager import (
    IdentityConflict,
    PairingBusy,
    PairingConflict,
    PairingManager,
    PeerGrant,
    PendingPairing,
    RelationshipState,
    RepairNotRequired,
    RepairRequired,
    TrustedPeer,
)
from .persistence import JsonStateStore
from .registry import NodeRegistry
from .remote_provider import AuthenticatedNodeProvider
from .remote_service import RemoteService
from .role_engine import RoleState
from .runtime import ConnectConfig, ConnectRuntime, RuntimeState, RuntimeStatus
from .server import RemoteSocketServer
from .sharing import CapabilityShare
from .transport_generation import (
    RotationConflict,
    TransportGeneration,
    TransportGenerationManager,
    TransportStateError,
)

__all__ = [
    "AuthenticatedNodeProvider",
    "CapabilityShare",
    "Cluster",
    "ClusterRole",
    "ConnectConfig",
    "ConnectRuntime",
    "ConnectionState",
    "DeviceHardwareHint",
    "DeviceHardwareProvider",
    "DeviceIdentity",
    "DeviceIdentityError",
    "DeviceIdentityView",
    "DiscoveredNodeCandidate",
    "EndpointCandidate",
    "EndpointSource",
    "FailoverCoordinator",
    "IdentityConflict",
    "JsonStateStore",
    "NetworkDiscovery",
    "NodeCapability",
    "NodeId",
    "NodeIdentity",
    "NodePermission",
    "NodeRegistry",
    "PairingBusy",
    "PairingConflict",
    "PairingManager",
    "PeerGrant",
    "PendingPairing",
    "RelationshipState",
    "RemoteService",
    "RemoteSocketServer",
    "RepairNotRequired",
    "RepairRequired",
    "RetryState",
    "RoleState",
    "RotationConflict",
    "RuntimeState",
    "RuntimeStatus",
    "TransportGeneration",
    "TransportGenerationManager",
    "TransportStateError",
    "TrustedPeer",
    "__version__",
    "verify_transport_proof",
]

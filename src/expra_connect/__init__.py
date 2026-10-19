"""Headless peer connection primitives."""

from ._version import __version__
from .cluster import Cluster, ClusterRole, FailoverCoordinator, RoleState
from .connections.state import ConnectionState, RetryState
from .data.permissions import (
    NodeCapability,
    NodePermission,
)
from .discovery.models import (
    DiscoveredNodeCandidate,
    EndpointCandidate,
    EndpointSource,
)
from .discovery.network import NetworkDiscovery
from .identity import (
    NodeId,
    NodeIdentity,
    verify_transport_proof,
)
from .identity.device_hardware import DeviceHardwareHint, DeviceHardwareProvider
from .identity.device_identity import (
    DeviceIdentity,
    DeviceIdentityError,
    DeviceIdentityView,
)
from .identity.transport_generation import (
    RotationConflict,
    TransportGeneration,
    TransportGenerationManager,
    TransportStateError,
)
from .nodes.registry import NodeRegistry
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
from .profile_migration import (
    ProfileMigrationError,
    ProfileMigrationResult,
    ProfileSourceReader,
    StagedProfileWriter,
    migrate_profile,
)
from .remote.provider import AuthenticatedNodeProvider
from .remote.service import RemoteService
from .runtime import ConnectConfig, ConnectRuntime, RuntimeState, RuntimeStatus
from .sharing.capabilities import CapabilityShare
from .transport.server import RemoteSocketServer

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
    "ProfileMigrationError",
    "ProfileMigrationResult",
    "ProfileSourceReader",
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
    "StagedProfileWriter",
    "TransportGeneration",
    "TransportGenerationManager",
    "TransportStateError",
    "TrustedPeer",
    "__version__",
    "migrate_profile",
    "verify_transport_proof",
]

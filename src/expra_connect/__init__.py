"""Headless peer connection primitives."""

from ._version import __version__
from .cluster import Cluster, ClusterRole
from .connection_state import ConnectionState, RetryState
from .discovery_full import NetworkDiscovery
from .failover import FailoverCoordinator
from .identity import NodeId, NodeIdentity
from .models import DiscoveredNodeCandidate, NodeCapability, NodePermission
from .pairing import PairingManager, PeerGrant, PendingPairing, TrustedPeer
from .persistence import JsonStateStore
from .registry import NodeRegistry
from .remote_service import AuthenticatedNodeProvider, RemoteService
from .role_engine import RoleState
from .runtime import ConnectConfig, ConnectRuntime, RuntimeState, RuntimeStatus
from .server import RemoteSocketServer
from .sharing import CapabilityShare

__all__ = [
    "AuthenticatedNodeProvider",
    "CapabilityShare",
    "Cluster",
    "ClusterRole",
    "ConnectConfig",
    "ConnectRuntime",
    "ConnectionState",
    "DiscoveredNodeCandidate",
    "FailoverCoordinator",
    "JsonStateStore",
    "NetworkDiscovery",
    "NodeCapability",
    "NodeId",
    "NodeIdentity",
    "NodePermission",
    "NodeRegistry",
    "PairingManager",
    "PeerGrant",
    "PendingPairing",
    "RemoteService",
    "RemoteSocketServer",
    "RetryState",
    "RoleState",
    "RuntimeState",
    "RuntimeStatus",
    "TrustedPeer",
    "__version__",
]

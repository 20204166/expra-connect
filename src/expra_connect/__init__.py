"""Headless peer connection primitives."""

from ._version import __version__
from .connection_state import ConnectionState, RetryState
from .discovery_full import NetworkDiscovery
from .failover import FailoverCoordinator
from .identity import NodeId, NodeIdentity
from .persistence import JsonStateStore
from .registry import NodeRegistry
from .remote_service import AuthenticatedNodeProvider, RemoteService
from .role_engine import RoleState
from .runtime import ConnectConfig, ConnectRuntime, RuntimeState, RuntimeStatus
from .server import RemoteSocketServer

__all__ = [
    "AuthenticatedNodeProvider",
    "ConnectConfig",
    "ConnectRuntime",
    "ConnectionState",
    "FailoverCoordinator",
    "JsonStateStore",
    "NetworkDiscovery",
    "NodeId",
    "NodeIdentity",
    "NodeRegistry",
    "RemoteService",
    "RemoteSocketServer",
    "RetryState",
    "RoleState",
    "RuntimeState",
    "RuntimeStatus",
    "__version__",
]

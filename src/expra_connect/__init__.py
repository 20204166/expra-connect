"""Headless peer connection primitives."""

from .connection_state import ConnectionState, RetryState
from .discovery_full import NetworkDiscovery
from .failover import FailoverCoordinator
from .identity import NodeId, NodeIdentity
from .persistence import JsonStateStore
from .registry import NodeRegistry
from .remote_service import AuthenticatedNodeProvider, RemoteService
from .role_engine import RoleState
from .server import RemoteSocketServer

__all__ = [
    "AuthenticatedNodeProvider",
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
]

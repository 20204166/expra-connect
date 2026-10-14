"""Neutral wire-domain values adapted from the peer core's public model."""

from __future__ import annotations

from enum import Enum


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

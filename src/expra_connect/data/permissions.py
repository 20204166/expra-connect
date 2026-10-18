"""Canonical node capability and permission vocabulary."""

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


READ_CAPABILITIES = frozenset({NodeCapability.READ_STATE})
READ_PERMISSIONS = frozenset({NodePermission.READ_STATE})

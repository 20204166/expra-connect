"""Canonical transport-neutral cluster package."""

from .failover import FailoverCoordinator
from .invites import ClusterDataError, decode_invite_blob, encode_invite_blob
from .models import (
    CapabilityGrant,
    ClusterRole,
    CoordinatorEpoch,
    CoordinatorLease,
    InviteRecord,
    PromotionDecision,
    RoleAssignment,
    RoleChange,
)
from .operations import Cluster, ClusterState
from .persistence import ClusterStore
from .roles import (
    HEARTBEAT_TIMEOUT_SECONDS,
    FencingError,
    RoleAuthorizationError,
    RoleState,
    can_promote,
    hash_invite,
    new_fencing_token,
    promote_subcoordinator,
    rejoin_as_worker,
    renew_lease,
)
from .state import role_state_from_dict, role_state_to_dict

__all__ = [
    "HEARTBEAT_TIMEOUT_SECONDS",
    "CapabilityGrant",
    "Cluster",
    "ClusterDataError",
    "ClusterRole",
    "ClusterState",
    "ClusterStore",
    "CoordinatorEpoch",
    "CoordinatorLease",
    "FailoverCoordinator",
    "FencingError",
    "InviteRecord",
    "PromotionDecision",
    "RoleAssignment",
    "RoleAuthorizationError",
    "RoleChange",
    "RoleState",
    "can_promote",
    "decode_invite_blob",
    "encode_invite_blob",
    "hash_invite",
    "new_fencing_token",
    "promote_subcoordinator",
    "rejoin_as_worker",
    "renew_lease",
    "role_state_from_dict",
    "role_state_to_dict",
]

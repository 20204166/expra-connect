"""Fencing and lease compatibility exports."""

from .roles import (
    HEARTBEAT_TIMEOUT_SECONDS,
    FencingError,
    can_promote,
    new_fencing_token,
    promote_subcoordinator,
    rejoin_as_worker,
    renew_lease,
)

__all__ = [
    "HEARTBEAT_TIMEOUT_SECONDS",
    "FencingError",
    "can_promote",
    "new_fencing_token",
    "promote_subcoordinator",
    "rejoin_as_worker",
    "renew_lease",
]

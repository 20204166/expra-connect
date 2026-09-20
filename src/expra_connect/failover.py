"""Compatibility re-export for canonical cluster failover."""

from .cluster.failover import FailoverCoordinator
from .cluster.roles import FencingError

__all__ = ["FailoverCoordinator", "FencingError"]

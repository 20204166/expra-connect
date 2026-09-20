"""Atomic cluster-state persistence and migration from the original schema."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ..persistence import JsonStateStore, StateDataError
from .invites import ClusterDataError


class ClusterStore:
    def __init__(self, path: Path):
        self.path = Path(path)

    def save(self, state: Any) -> None:
        JsonStateStore(self.path).save(state.to_dict())

    def load(self) -> Any:
        from .operations import ClusterState

        try:
            value = JsonStateStore(self.path).load()
        except StateDataError as error:
            raise ClusterDataError("persisted cluster state is invalid") from error
        return ClusterState.from_dict(value)

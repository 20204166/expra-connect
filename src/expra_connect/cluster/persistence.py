"""Atomic cluster-state persistence and migration from the original schema."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ..persistence import StateDataError, YamlStateStore
from .invites import ClusterDataError


class ClusterStore:
    def __init__(self, path: Path):
        self.path = Path(path)

    def exists(self) -> bool:
        return YamlStateStore(self.path).exists()

    def save(self, state: Any) -> None:
        YamlStateStore(self.path).save(state.to_dict())

    def load(self) -> Any:
        from .operations import ClusterState

        try:
            value = YamlStateStore(self.path).load()
        except StateDataError as error:
            raise ClusterDataError("persisted cluster state is invalid") from error
        return ClusterState.from_dict(value)

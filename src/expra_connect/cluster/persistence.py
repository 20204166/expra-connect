"""Atomic cluster-state persistence and migration from the original schema."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ..persistence import StateDataError, YamlStateStore
from .invites import ClusterDataError

if TYPE_CHECKING:
    from .operations import ClusterState


class ClusterStore:
    def __init__(self, path: Path):
        self.path = Path(path)

    def exists(self) -> bool:
        return YamlStateStore(self.path).exists()

    def save(self, state: ClusterState | Mapping[str, Any]) -> None:
        from .operations import ClusterState

        document = state.to_dict() if isinstance(state, ClusterState) else dict(state)
        YamlStateStore(self.path).save(document)

    def load(self) -> ClusterState:
        from .operations import ClusterState

        try:
            value = YamlStateStore(self.path).load()
        except StateDataError as error:
            raise ClusterDataError("persisted cluster state is invalid") from error
        return ClusterState.from_dict(value)

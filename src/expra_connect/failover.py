"""Durable orchestration around the pure role/fencing decisions."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace

from .identity import NodeId
from .role_engine import (
    FencingError,
    RoleState,
    promote_subcoordinator,
    rejoin_as_worker,
    renew_lease,
)


class FailoverCoordinator:
    """Apply role decisions and persist each accepted epoch transition."""

    def __init__(
        self,
        state: RoleState,
        *,
        persist: Callable[[RoleState], None] | None = None,
    ) -> None:
        self.state = state
        self._persist = persist

    def heartbeat(
        self,
        *,
        coordinator_id: NodeId,
        fencing_token: str,
        now: float,
    ) -> bool:
        if self.state.epoch is None:
            return False
        try:
            epoch = renew_lease(
                self.state.epoch,
                coordinator_id=coordinator_id,
                fencing_token=fencing_token,
                now=now,
            )
        except FencingError:
            return False
        self.state = replace(self.state, epoch=epoch)
        self._save()
        return True

    def promote_if_due(self, *, now: float) -> bool:
        if self.state.epoch is None:
            return False
        try:
            decision = promote_subcoordinator(self.state, now=now)
        except FencingError:
            return False
        self.state = replace(
            self.state,
            assignments=decision.assignments,
            epoch=decision.epoch,
            promotion_epochs=self.state.promotion_epochs | {decision.epoch.epoch - 1},
        )
        self._save()
        return True

    def rejoin_as_worker(self, *, node_id: NodeId, current_epoch: int) -> None:
        self.state = rejoin_as_worker(
            self.state, node_id=node_id, current_epoch=current_epoch
        )
        self._save()

    def _save(self) -> None:
        if self._persist is not None:
            self._persist(self.state)

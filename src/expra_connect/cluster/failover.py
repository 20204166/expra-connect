"""Durable orchestration around pure failover decisions."""

from __future__ import annotations

import threading
from collections.abc import Callable
from dataclasses import replace

from ..identity import NodeId
from .roles import (
    FencingError,
    RoleState,
    promote_subcoordinator,
    rejoin_as_worker,
    renew_lease,
)


class FailoverCoordinator:
    def __init__(
        self, state: RoleState, *, persist: Callable[[RoleState], None] | None = None
    ) -> None:
        self.state, self._persist = state, persist
        self._lock = threading.RLock()

    def _commit(self, candidate: RoleState) -> None:
        if self._persist is not None:
            self._persist(candidate)
        self.state = candidate

    def heartbeat(
        self, *, coordinator_id: NodeId, fencing_token: str, now: float
    ) -> bool:
        with self._lock:
            if self.state.epoch is None:
                return False
            try:
                candidate = replace(
                    self.state,
                    epoch=renew_lease(
                        self.state.epoch,
                        coordinator_id=coordinator_id,
                        fencing_token=fencing_token,
                        now=now,
                    ),
                )
            except FencingError:
                return False
            self._commit(candidate)
            return True

    def promote_if_due(self, *, now: float) -> bool:
        with self._lock:
            try:
                decision = promote_subcoordinator(self.state, now=now)
            except FencingError:
                return False
            candidate = replace(
                self.state,
                assignments=decision.assignments,
                epoch=decision.epoch,
                promotion_epochs=self.state.promotion_epochs
                | {decision.epoch.epoch - 1},
            )
            self._commit(candidate)
            return True

    def rejoin_as_worker(self, *, node_id: NodeId, current_epoch: int) -> None:
        with self._lock:
            self._commit(
                rejoin_as_worker(
                    self.state, node_id=node_id, current_epoch=current_epoch
                )
            )

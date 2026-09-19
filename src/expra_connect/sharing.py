"""Generic target-owned capability sharing."""

from __future__ import annotations

from collections.abc import Callable

from .identity import NodeId


class CapabilityShare:
    def __init__(self) -> None:
        self._handlers: dict[str, Callable[[NodeId, dict[str, object]], object]] = {}
        self._allowed: dict[NodeId, set[str]] = {}

    def register(
        self, capability: str, handler: Callable[[NodeId, dict[str, object]], object]
    ) -> None:
        if not capability or capability in self._handlers:
            raise ValueError("invalid or duplicate capability")
        self._handlers[capability] = handler

    def allow(self, peer_id: NodeId, capability: str) -> None:
        if capability not in self._handlers:
            raise ValueError("unknown capability")
        self._allowed.setdefault(peer_id, set()).add(capability)

    def request(
        self, peer_id: NodeId, capability: str, params: dict[str, object] | None = None
    ) -> object:
        if capability not in self._allowed.get(peer_id, set()):
            raise PermissionError("capability is not authorized")
        return self._handlers[capability](peer_id, params or {})

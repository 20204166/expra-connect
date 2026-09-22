"""Generic target-owned capability sharing."""

from __future__ import annotations

import threading
from collections.abc import Callable

from .identity import NodeId

MAX_CAPABILITY_ID_LENGTH = 128
CapabilityHandler = Callable[[NodeId, dict[str, object]], object]


def validate_capability_id(capability: object) -> None:
    """Validate an opaque capability identifier without normalizing it."""

    if not isinstance(capability, str):
        raise TypeError("capability must be a string")
    if not capability.strip() or len(capability) > MAX_CAPABILITY_ID_LENGTH:
        raise ValueError("invalid capability")


def _is_valid_capability_id(capability: object) -> bool:
    try:
        validate_capability_id(capability)
    except (TypeError, ValueError):
        return False
    return True


class CapabilityShare:
    """Own explicit, ephemeral, per-peer read-only capability grants."""

    def __init__(self) -> None:
        self._handlers: dict[str, CapabilityHandler] = {}
        self._allowed: dict[NodeId, set[str]] = {}
        self._grant_generations: dict[tuple[NodeId, str], int] = {}
        self._next_generation = 0
        self._lock = threading.RLock()

    def register(self, capability: str, handler: CapabilityHandler) -> None:
        validate_capability_id(capability)
        if not callable(handler):
            raise TypeError("capability handler must be callable")
        with self._lock:
            if capability in self._handlers:
                raise ValueError("invalid or duplicate capability")
            self._handlers[capability] = handler

    def allow(self, peer_id: NodeId, capability: str) -> None:
        self._validate_peer(peer_id)
        validate_capability_id(capability)
        with self._lock:
            if capability not in self._handlers:
                raise ValueError("unknown capability")
            allowed = self._allowed.setdefault(peer_id, set())
            if capability in allowed:
                return
            allowed.add(capability)
            self._next_generation += 1
            self._grant_generations[(peer_id, capability)] = self._next_generation

    def revoke(self, peer_id: NodeId, capability: str) -> None:
        """Remove one target-side grant without affecting other capabilities."""

        self._validate_peer(peer_id)
        validate_capability_id(capability)
        with self._lock:
            allowed = self._allowed.get(peer_id)
            if allowed is None:
                return
            allowed.discard(capability)
            self._grant_generations.pop((peer_id, capability), None)
            if not allowed:
                self._allowed.pop(peer_id, None)

    def revoke_peer(self, peer_id: NodeId) -> None:
        """Remove every generic capability grant owned by one peer."""

        self._validate_peer(peer_id)
        with self._lock:
            allowed = self._allowed.pop(peer_id, None)
            if allowed is None:
                return
            for capability in allowed:
                self._grant_generations.pop((peer_id, capability), None)

    def clear_grants(self) -> None:
        """Clear ephemeral grants while retaining registered handlers."""

        with self._lock:
            self._allowed.clear()
            self._grant_generations.clear()

    def request(
        self, peer_id: NodeId, capability: str, params: dict[str, object] | None = None
    ) -> object:
        self._validate_peer(peer_id)
        validate_capability_id(capability)
        request_params = {} if params is None else params
        with self._lock:
            allowed = self._allowed.get(peer_id)
            handler = self._handlers.get(capability)
            generation = self._grant_generations.get((peer_id, capability))
            if (
                handler is None
                or allowed is None
                or capability not in allowed
                or generation is None
            ):
                raise PermissionError("capability is not authorized")

        result = handler(peer_id, request_params)

        with self._lock:
            current_allowed = self._allowed.get(peer_id)
            if (
                current_allowed is not None
                and capability in current_allowed
                and self._grant_generations.get((peer_id, capability)) == generation
                and self._handlers.get(capability) is handler
            ):
                return result
        raise PermissionError("capability authorization changed during request")

    @staticmethod
    def _validate_peer(peer_id: NodeId) -> None:
        if not isinstance(peer_id, NodeId):
            raise TypeError("peer id must be a NodeId")

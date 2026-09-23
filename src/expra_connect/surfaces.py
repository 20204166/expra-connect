"""Application-neutral structured surface registration and authorization."""

from __future__ import annotations

import math
import time
from collections.abc import Callable, Hashable, Iterable, Mapping
from dataclasses import dataclass
from enum import Enum
from types import MappingProxyType
from typing import cast

from .identity import NodeId
from .surface_protocol import MAX_SURFACE_ID_LENGTH

SurfaceHandler = Callable[[NodeId, dict[str, object]], object]


class SurfaceAccess(str, Enum):
    READ = "read"
    REVIEW = "review"
    ACTION = "action"


class SurfaceHandlerError(RuntimeError):
    """Raised when a host-owned surface handler fails."""


@dataclass(frozen=True, slots=True)
class SurfaceDefinition:
    surface_id: str
    read: SurfaceHandler
    review: SurfaceHandler | None
    actions: Mapping[str, SurfaceHandler]


@dataclass(frozen=True, slots=True)
class SurfaceGrant:
    source: Hashable
    surface_id: str
    access: SurfaceAccess
    expires_at: float | None
    cluster: bool


class SurfaceRegistry:
    """Keep ephemeral surface handlers and their explicit access grants."""

    MAX_ID_LENGTH = MAX_SURFACE_ID_LENGTH

    def __init__(self, *, clock: Callable[[], float] = time.time) -> None:
        self._clock = clock
        self._definitions: dict[str, SurfaceDefinition] = {}
        self._peer_grants: dict[tuple[NodeId, str, SurfaceAccess], SurfaceGrant] = {}
        self._cluster_grants: dict[
            tuple[Hashable, str, SurfaceAccess], SurfaceGrant
        ] = {}

    def register(
        self,
        surface_id: str,
        *,
        read: SurfaceHandler,
        review: SurfaceHandler | None = None,
        actions: Mapping[str, SurfaceHandler]
        | Iterable[tuple[str, SurfaceHandler]]
        | None = None,
    ) -> SurfaceDefinition:
        self._validate_id(surface_id, "surface")
        if surface_id in self._definitions:
            raise ValueError("duplicate surface")
        if not callable(read) or (review is not None and not callable(review)):
            raise TypeError("surface handlers must be callable")
        action_items: list[tuple[str, SurfaceHandler]]
        if actions is None:
            action_items = []
        elif isinstance(actions, Mapping):
            action_items = list(
                cast(Iterable[tuple[str, SurfaceHandler]], actions.items())
            )
        else:
            action_items = list(cast(Iterable[tuple[str, SurfaceHandler]], actions))
        action_handlers: dict[str, SurfaceHandler] = {}
        for action_id, handler in action_items:
            self._validate_id(action_id, "action")
            if action_id in action_handlers:
                raise ValueError("duplicate surface action")
            if not callable(handler):
                raise TypeError("surface handlers must be callable")
            action_handlers[action_id] = handler
        definition = SurfaceDefinition(
            surface_id,
            read,
            review,
            MappingProxyType(action_handlers),
        )
        self._definitions[surface_id] = definition
        return definition

    def grant_peer(
        self,
        peer_id: NodeId,
        surface_id: str,
        *,
        access: SurfaceAccess | str,
        expires_at: float | None = None,
    ) -> SurfaceGrant:
        if not isinstance(peer_id, NodeId):
            raise TypeError("peer id must be a NodeId")
        grant = self._new_grant(peer_id, surface_id, access, expires_at, cluster=False)
        self._peer_grants[(peer_id, surface_id, grant.access)] = grant
        return grant

    def grant_cluster(
        self,
        source: Hashable,
        surface_id: str,
        *,
        access: SurfaceAccess | str,
        expires_at: float | None = None,
    ) -> SurfaceGrant:
        self._validate_source(source)
        grant = self._new_grant(source, surface_id, access, expires_at, cluster=True)
        self._cluster_grants[(source, surface_id, grant.access)] = grant
        return grant

    def revoke_peer(
        self,
        peer_id: NodeId,
        surface_id: str,
        *,
        access: SurfaceAccess | str | None = None,
    ) -> None:
        if access is None:
            for key in tuple(self._peer_grants):
                if key[0] == peer_id and key[1] == surface_id:
                    del self._peer_grants[key]
            return
        normalized = self._access(access)
        self._peer_grants.pop((peer_id, surface_id, normalized), None)

    def stop_peer(self, peer_id: NodeId, surface_id: str) -> None:
        self.revoke_peer(peer_id, surface_id)

    def revoke_source(self, source: Hashable) -> None:
        """Remove every direct or cluster grant owned by one authorization source."""
        self._peer_grants = {
            key: grant for key, grant in self._peer_grants.items() if key[0] != source
        }
        self._cluster_grants = {
            key: grant
            for key, grant in self._cluster_grants.items()
            if key[0] != source
        }

    def clear_grants(self) -> None:
        """Clear ephemeral grants while retaining registered handlers."""
        self._peer_grants.clear()
        self._cluster_grants.clear()

    def dispatch(
        self,
        peer_id: NodeId,
        surface_id: str,
        *,
        access: SurfaceAccess | str,
        action: str | None = None,
        params: dict[str, object] | None = None,
        cluster_sources: Iterable[Hashable] = (),
        now: float | None = None,
    ) -> object:
        if not isinstance(peer_id, NodeId):
            raise TypeError("peer id must be a NodeId")
        normalized = self._access(access)
        current = self._clock() if now is None else now
        grant = self._peer_grants.get((peer_id, surface_id, normalized))
        if not self._valid(grant, current):
            grant = next(
                (
                    candidate
                    for source in cluster_sources
                    if (
                        candidate := self._cluster_grants.get(
                            (source, surface_id, normalized)
                        )
                    )
                    and self._valid(candidate, current)
                ),
                None,
            )
        if grant is None:
            raise PermissionError("surface access is not authorized")

        definition = self._definitions.get(surface_id)
        if definition is None:
            raise ValueError("unknown surface")
        handler = self._handler(definition, normalized, action)
        try:
            return handler(peer_id, {} if params is None else params)
        except Exception as error:
            raise SurfaceHandlerError("surface handler failed") from error

    def _new_grant(
        self,
        source: Hashable,
        surface_id: str,
        access: SurfaceAccess | str,
        expires_at: float | None,
        *,
        cluster: bool,
    ) -> SurfaceGrant:
        if surface_id not in self._definitions:
            raise ValueError("unknown surface")
        self._validate_id(surface_id, "surface")
        normalized_expiry = self._expiry(expires_at)
        return SurfaceGrant(
            source, surface_id, self._access(access), normalized_expiry, cluster
        )

    @staticmethod
    def _access(access: SurfaceAccess | str) -> SurfaceAccess:
        try:
            return SurfaceAccess(access)
        except (TypeError, ValueError) as error:
            raise ValueError("invalid surface access") from error

    @classmethod
    def _validate_id(cls, value: str, kind: str) -> None:
        if not isinstance(value, str):
            raise TypeError(f"{kind} id must be text")
        if not value.strip() or len(value) > MAX_SURFACE_ID_LENGTH:
            raise ValueError(f"invalid {kind} id")

    @staticmethod
    def _validate_source(source: Hashable) -> None:
        if source is None:
            raise ValueError("invalid cluster source")
        try:
            hash(source)
        except TypeError as error:
            raise TypeError("cluster source must be hashable") from error

    @staticmethod
    def _expiry(expires_at: float | None) -> float | None:
        if expires_at is None:
            return None
        if isinstance(expires_at, bool) or not isinstance(expires_at, (int, float)):
            raise TypeError("expiry must be numeric")
        if not math.isfinite(float(expires_at)):
            raise ValueError("expiry must be finite")
        return float(expires_at)

    @staticmethod
    def _valid(grant: SurfaceGrant | None, now: float) -> bool:
        return grant is not None and (
            grant.expires_at is None or grant.expires_at >= now
        )

    @staticmethod
    def _handler(
        definition: SurfaceDefinition,
        access: SurfaceAccess,
        action: str | None,
    ) -> SurfaceHandler:
        if access is SurfaceAccess.READ:
            return definition.read
        if access is SurfaceAccess.REVIEW:
            if definition.review is None:
                raise PermissionError("surface review is not available")
            return definition.review
        if action is None:
            raise ValueError("surface action is required")
        if not isinstance(action, str):
            raise TypeError("action id must be text")
        try:
            return definition.actions[action]
        except KeyError as error:
            raise ValueError("unknown surface action") from error


class SurfaceRuntimeMixin:
    """Public runtime seam for the process-local surface registry."""

    _surface_registry: SurfaceRegistry

    def register_surface(
        self,
        surface_id: str,
        *,
        read: SurfaceHandler,
        review: SurfaceHandler | None = None,
        actions: Mapping[str, SurfaceHandler]
        | Iterable[tuple[str, SurfaceHandler]]
        | None = None,
    ) -> SurfaceDefinition:
        return self._surface_registry.register(
            surface_id, read=read, review=review, actions=actions
        )

    def grant_surface_access(
        self,
        peer_id: NodeId,
        surface_id: str,
        *,
        access: SurfaceAccess | str,
        expires_at: float | None = None,
    ) -> SurfaceGrant:
        return self._surface_registry.grant_peer(
            peer_id, surface_id, access=access, expires_at=expires_at
        )

    def grant_cluster_surface_access(
        self,
        source: Hashable,
        surface_id: str,
        *,
        access: SurfaceAccess | str,
        expires_at: float | None = None,
    ) -> SurfaceGrant:
        """Grant a surface to an explicit, separately validated cluster source."""
        return self._surface_registry.grant_cluster(
            source, surface_id, access=access, expires_at=expires_at
        )

    def revoke_surface_access(
        self,
        peer_id: NodeId,
        surface_id: str,
        *,
        access: SurfaceAccess | str | None = None,
    ) -> None:
        self._surface_registry.revoke_peer(peer_id, surface_id, access=access)

    def stop_surface_share(self, peer_id: NodeId, surface_id: str) -> None:
        self._surface_registry.stop_peer(peer_id, surface_id)

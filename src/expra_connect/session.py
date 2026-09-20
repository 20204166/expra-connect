"""Authenticated logical sessions independent from physical sockets."""

import secrets
import threading
import time
from collections.abc import Callable

from .wire_protocol import RemoteAuthError, RemoteRequest


class LogicalSessionRegistry:
    """Tracks the active connection generation and fences retired sockets."""

    def __init__(
        self, *, clock: Callable[[], float] = time.time, ttl_seconds: float = 600.0
    ) -> None:
        self._clock = clock
        self._ttl = ttl_seconds
        self._sessions: dict[str, tuple[str, str, float, set[str]]] = {}
        self._lock = threading.Lock()

    def authenticate(
        self, request: RemoteRequest, *, connection_generation: str | None
    ) -> str:
        generation = connection_generation or "memory"
        owner = (
            request.caller_node_id.value
            if request.caller_node_id is not None
            else request.node_id.value
        )
        if request.session_id is None:
            session_id = secrets.token_hex(16)
            with self._lock:
                self._sessions[session_id] = (
                    owner,
                    generation,
                    self._clock() + self._ttl,
                    set(),
                )
            return session_id
        with self._lock:
            session = self._sessions.get(request.session_id)
            if session is None or self._clock() >= session[2]:
                self._sessions.pop(request.session_id, None)
                raise RemoteAuthError("logical session is expired")
            active_owner, active_generation, expires_at, retired = session
            if active_owner != owner:
                raise RemoteAuthError("logical session belongs to another identity")
            if generation in retired:
                raise RemoteAuthError("connection generation is retired")
            if generation != active_generation:
                if not request.resume:
                    raise RemoteAuthError("connection generation is stale")
                retired.add(active_generation)
                self._sessions[request.session_id] = (
                    owner,
                    generation,
                    expires_at,
                    retired,
                )
            return request.session_id

    def assert_current(self, session_id: str, generation: str | None) -> None:
        if generation is None:
            return
        with self._lock:
            session = self._sessions.get(session_id)
            if session is None or session[1] != generation:
                raise RemoteAuthError("connection generation is stale")

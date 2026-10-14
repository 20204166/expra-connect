"""Authenticated logical sessions independent from physical sockets.

The registry is the canonical owner of logical-session lifetime and of which
physical ``connection_generation`` may publish a result for an established
session. It never interprets transport, pairing, or replay state: a generation
is an opaque equality/retirement token.
"""

from __future__ import annotations

import math
import secrets
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field

from .wire_protocol import MAX_IDENTIFIER_LENGTH, RemoteAuthError, RemoteRequest

DEFAULT_SESSION_TTL_SECONDS = 600.0
DEFAULT_MAX_SESSIONS = 1024
DEFAULT_MAX_GENERATIONS_PER_SESSION = 64


@dataclass(slots=True)
class _LogicalSession:
    owner: str
    active_generation: str | None
    expires_at: float
    retired: set[str | None] = field(default_factory=set)


def _finite_positive(value: object, *, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(f"{name} must be a finite positive number")
    if not math.isfinite(float(value)) or float(value) <= 0:
        raise ValueError(f"{name} must be a finite positive number")
    return float(value)


def _positive_int(value: object, *, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{name} must be a positive integer")
    if value < 1:
        raise ValueError(f"{name} must be a positive integer")
    return value


def _valid_token(value: object) -> bool:
    return isinstance(value, str) and 1 <= len(value) <= MAX_IDENTIFIER_LENGTH


class LogicalSessionRegistry:
    """Tracks the active connection generation and fences retired sockets."""

    def __init__(
        self,
        *,
        clock: Callable[[], float] = time.monotonic,
        ttl_seconds: float = DEFAULT_SESSION_TTL_SECONDS,
        max_sessions: int = DEFAULT_MAX_SESSIONS,
        max_generations_per_session: int = DEFAULT_MAX_GENERATIONS_PER_SESSION,
        session_id_factory: Callable[[], str] | None = None,
    ) -> None:
        self._ttl = _finite_positive(ttl_seconds, name="session ttl")
        self._max_sessions = _positive_int(max_sessions, name="max sessions")
        self._max_generations = _positive_int(
            max_generations_per_session, name="max generations per session"
        )
        self._clock = clock
        self._sessions: dict[str, _LogicalSession] = {}
        self._lock = threading.Lock()
        self._session_id_factory = (
            session_id_factory
            if session_id_factory is not None
            else lambda: secrets.token_hex(16)
        )

    def authenticate(
        self, request: RemoteRequest, *, connection_generation: str | None
    ) -> str:
        generation = self._validate_generation(connection_generation)
        owner = (
            request.caller_node_id.value
            if request.caller_node_id is not None
            else request.node_id.value
        )
        now = self._validated_clock()
        if request.session_id is None:
            return self._create(owner, generation, now)
        session_id = request.session_id
        if not _valid_token(session_id):
            raise RemoteAuthError("logical session id is invalid")
        with self._lock:
            session = self._sessions.get(session_id)
            if session is None or now >= session.expires_at:
                self._sessions.pop(session_id, None)
                raise RemoteAuthError("logical session is expired")
            if session.owner != owner:
                raise RemoteAuthError("logical session belongs to another identity")
            if generation in session.retired:
                raise RemoteAuthError("connection generation is retired")
            if generation != session.active_generation:
                if not request.resume:
                    raise RemoteAuthError("connection generation is stale")
                if len(session.retired) >= self._max_generations:
                    # Fail closed: never forget a retired generation while the
                    # session stays usable, or a stale socket could return.
                    self._sessions.pop(session_id, None)
                    raise RemoteAuthError(
                        "logical session retired-generation limit exceeded"
                    )
                session.retired.add(session.active_generation)
                session.active_generation = generation
            return session_id

    def assert_current(self, session_id: str, generation: str | None) -> None:
        token = self._validate_generation(generation)
        if not _valid_token(session_id):
            raise RemoteAuthError("logical session id is invalid")
        now = self._validated_clock()
        with self._lock:
            session = self._sessions.get(session_id)
            if session is None or now >= session.expires_at:
                self._sessions.pop(session_id, None)
                raise RemoteAuthError("logical session is expired")
            if session.active_generation != token:
                raise RemoteAuthError("connection generation is stale")

    def _create(self, owner: str, generation: str | None, now: float) -> str:
        with self._lock:
            self._prune_locked(now)
            if len(self._sessions) >= self._max_sessions:
                raise RemoteAuthError("logical session limit reached")
            session_id = self._allocate_id_locked()
            self._sessions[session_id] = _LogicalSession(
                owner=owner,
                active_generation=generation,
                expires_at=now + self._ttl,
            )
            return session_id

    def _allocate_id_locked(self) -> str:
        for _ in range(100):
            candidate = self._session_id_factory()
            if not _valid_token(candidate):
                raise RemoteAuthError("logical session id is invalid")
            if candidate not in self._sessions:
                return candidate
        raise RemoteAuthError("could not allocate a logical session")

    def _prune_locked(self, now: float) -> None:
        expired = [
            key for key, session in self._sessions.items() if now >= session.expires_at
        ]
        for key in expired:
            del self._sessions[key]

    def _validated_clock(self) -> float:
        value = self._clock()
        if (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(float(value))
        ):
            raise RemoteAuthError("logical session clock is invalid")
        return float(value)

    def _validate_generation(self, generation: str | None) -> str | None:
        if generation is None:
            return None
        if not _valid_token(generation):
            raise RemoteAuthError("connection generation is invalid")
        return generation

"""Connection axis and bounded retry policy, independent of trust or membership."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum


class ConnectionStatus(str, Enum):
    UNKNOWN = "unknown"
    CONNECTING = "connecting"
    ONLINE = "online"
    OFFLINE = "offline"
    AUTHENTICATION_FAILED = "authentication_failed"
    IDENTITY_CHANGED = "identity_changed"


class PeerFailure(str, Enum):
    TIMEOUT = "timeout"
    CONNECTION_REFUSED = "connection_refused"
    ROUTE_FAILURE = "route_failure"
    DISAPPEARED = "disappeared"
    AUTHENTICATION_FAILED = "authentication_failed"
    IDENTITY_CHANGED = "identity_changed"


def classify_peer_failure(error: BaseException | str) -> PeerFailure:
    name = type(error).__name__ if isinstance(error, BaseException) else ""
    message = str(error).lower()
    if name in {"RemoteAuthError", "RemoteProtocolError"} or any(
        marker in message
        for marker in ("auth", "identity", "fingerprint", "certificate")
    ):
        return PeerFailure.AUTHENTICATION_FAILED
    if "timeout" in message or "timed out" in message:
        return PeerFailure.TIMEOUT
    if "refused" in message:
        return PeerFailure.CONNECTION_REFUSED
    if "route" in message or "network is unreachable" in message:
        return PeerFailure.ROUTE_FAILURE
    return PeerFailure.DISAPPEARED


@dataclass(frozen=True, slots=True)
class ConnectionState:
    status: ConnectionStatus
    reason: str | None = None
    changed_at: float | None = None

    @classmethod
    def unknown(cls, *, now: float | None = None) -> ConnectionState:
        return cls(ConnectionStatus.UNKNOWN, changed_at=now)

    @classmethod
    def connecting(cls, *, now: float | None = None) -> ConnectionState:
        return cls(ConnectionStatus.CONNECTING, changed_at=now)

    @classmethod
    def online(cls, *, now: float | None = None) -> ConnectionState:
        return cls(ConnectionStatus.ONLINE, changed_at=now)

    @classmethod
    def offline(
        cls, reason: str | None = None, *, now: float | None = None
    ) -> ConnectionState:
        return cls(ConnectionStatus.OFFLINE, reason=reason, changed_at=now)


@dataclass(slots=True)
class RetryState:
    attempt: int = 0
    next_attempt_at: float | None = None
    automatic_retry: bool = True
    last_failure: PeerFailure | None = None

    def record_failure(
        self,
        failure: PeerFailure,
        *,
        now: float,
        jitter: Callable[[int], float] | None = None,
        base_seconds: float = 1.0,
        max_seconds: float = 300.0,
        max_backoff_exponent: int = 8,
    ) -> None:
        self.last_failure = failure
        self.attempt += 1
        if failure in {
            PeerFailure.AUTHENTICATION_FAILED,
            PeerFailure.IDENTITY_CHANGED,
        }:
            self.next_attempt_at = None
            self.automatic_retry = False
            return
        delay = min(
            max_seconds,
            base_seconds * 2 ** min(self.attempt - 1, max_backoff_exponent),
        )
        extra = 0.0 if jitter is None else max(0.0, float(jitter(self.attempt)))
        self.next_attempt_at = now + delay + extra
        self.automatic_retry = True

    def reset(self) -> None:
        self.attempt = 0
        self.next_attempt_at = None
        self.automatic_retry = True
        self.last_failure = None

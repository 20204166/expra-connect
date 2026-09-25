"""Bounded, thread-safe runtime observability primitives."""

from __future__ import annotations

import logging
import math
import statistics
import threading
import time
from collections import deque
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Literal

LOGGER = logging.getLogger(__name__)

Outcome = Literal["success", "failure", "cancelled"]
EventKind = Literal[
    "request",
    "commit",
    "failure",
    "coalesced",
    "stale",
    "rejected",
    "cache_hit",
]
_EVENT_KINDS: frozenset[str] = frozenset(
    {
        "request",
        "commit",
        "failure",
        "coalesced",
        "stale",
        "rejected",
        "cache_hit",
    }
)
_COUNTED_EVENT_KINDS: frozenset[str] = frozenset({"coalesced", "stale", "rejected"})

_NO_WATCHER: object = object()


@dataclass(frozen=True, slots=True)
class ObservationToken:
    target: str
    started: float
    identifier: int
    watcher_id: object = _NO_WATCHER
    tracked: bool = True


class FrozenDistribution(Mapping[str, float | int]):
    """Deeply read-only mapping that deserializes back to a plain dict."""

    __slots__ = ("_data",)

    def __init__(self, data: Mapping[str, float | int]) -> None:
        self._data = dict(data)

    def __getitem__(self, key: str) -> float | int:
        return self._data[key]

    def __iter__(self) -> Iterator[str]:
        return iter(self._data)

    def __len__(self) -> int:
        return len(self._data)

    def __reduce__(
        self,
    ) -> tuple[type[dict[str, float | int]], tuple[dict[str, float | int]]]:
        return (dict, (self._data,))

    def __repr__(self) -> str:
        return repr(self._data)

    def __eq__(self, other: object) -> bool:
        if isinstance(other, FrozenDistribution):
            return self._data == other._data
        if isinstance(other, dict):
            return self._data == other
        return NotImplemented


@dataclass(frozen=True, slots=True)
class MetricSnapshot:
    target: str
    count: int
    successes: int
    failures: int
    cancellations: int
    coalesced: int
    stale: int
    rejected: int
    events: tuple[tuple[str, int], ...]
    in_flight: int
    peak_in_flight: int
    samples: tuple[float, ...]
    distribution: Mapping[str, float | int]
    last_error: str | None


@dataclass(frozen=True, slots=True)
class ObservabilitySnapshot:
    session_started_at: float
    captured_at: float
    duration_seconds: float
    metrics: tuple[MetricSnapshot, ...]


@dataclass(slots=True)
class _Metric:
    samples: deque[float]
    count: int = 0
    successes: int = 0
    failures: int = 0
    cancellations: int = 0
    in_flight: int = 0
    peak_in_flight: int = 0
    last_error: str | None = None
    coalesced: int = 0
    stale: int = 0
    rejected: int = 0
    events: dict[str, int] = field(default_factory=dict)


def _percentile(values: list[float], fraction: float) -> float:
    if len(values) == 1:
        return values[0]
    position = (len(values) - 1) * fraction
    lower = int(position)
    upper = min(lower + 1, len(values) - 1)
    return values[lower] + (values[upper] - values[lower]) * (position - lower)


def summarize_samples(samples: tuple[float, ...]) -> dict[str, float | int]:
    """Return distribution statistics for a non-empty bounded sample set."""

    if not samples:
        raise ValueError("at least one sample is required")
    for value in samples:
        if (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(value)
        ):
            raise ValueError("samples must be finite real numbers")
    ordered = sorted(samples)
    p25 = _percentile(ordered, 0.25)
    p75 = _percentile(ordered, 0.75)
    median = statistics.median(ordered)
    return {
        "minimum": ordered[0],
        "p25": p25,
        "p50": median,
        "median": median,
        "p75": p75,
        "p95": _percentile(ordered, 0.95),
        "p99": _percentile(ordered, 0.99),
        "maximum": ordered[-1],
        "spread": ordered[-1] - ordered[0],
        "outliers": sum(value > p75 + 1.5 * (p75 - p25) for value in ordered),
    }


@contextmanager
def observation_scope(
    observer: ObservabilityWatcher | None,
    target: str,
    *,
    cancelled: Callable[[], bool] | None = None,
) -> Iterator[None]:
    """Finish one observation with a stable outcome when a scope raises.

    Observability is non-authoritative: a failing observer (or ``cancelled``
    predicate) must never replace the application's own result.  ``begin``,
    ``finish`` and ``cancelled`` failures are contained so the wrapped body's
    outcome is preserved verbatim.
    """

    token: ObservationToken | None = None
    if observer is not None:
        try:
            token = observer.begin(target)
        except Exception:  # noqa: BLE001 - observability is non-authoritative.
            token = None
    outcome: Outcome = "success"
    try:
        yield
    except BaseException:
        outcome = "failure"
        if cancelled is not None:
            try:
                if cancelled():
                    outcome = "cancelled"
            except Exception:  # noqa: BLE001 - a broken cancel check must not mask the app error.
                outcome = "failure"
        raise
    finally:
        if observer is not None and token is not None:
            try:
                observer.finish(token, outcome=outcome)
            except Exception:
                LOGGER.debug("Observability finish failed", exc_info=True)


class ObservabilityWatcher:
    """Collect bounded runtime metrics without owning application behavior."""

    def __init__(
        self,
        *,
        sample_limit: int = 128,
        max_targets: int = 512,
        max_active: int = 1024,
        clock: Callable[[], float] = time.perf_counter,
        wall_clock: Callable[[], float] = time.time,
    ) -> None:
        if (
            isinstance(sample_limit, bool)
            or not isinstance(sample_limit, int)
            or sample_limit <= 0
        ):
            raise ValueError("sample_limit must be a positive integer")
        if (
            isinstance(max_targets, bool)
            or not isinstance(max_targets, int)
            or max_targets <= 0
        ):
            raise ValueError("max_targets must be a positive integer")
        if (
            isinstance(max_active, bool)
            or not isinstance(max_active, int)
            or max_active <= 0
        ):
            raise ValueError("max_active must be a positive integer")
        self._sample_limit = sample_limit
        self._max_targets = max_targets
        self._max_active = max_active
        self._clock = clock
        self._wall_clock = wall_clock
        self._session_started_at = self._require_finite_clock(
            wall_clock(), name="wall_clock"
        )
        self._lock = threading.RLock()
        self._metrics: dict[str, _Metric] = {}
        self._active_tokens: dict[int, ObservationToken] = {}
        self._next_token = 0
        self._watcher_id = object()

    def begin(self, target: str) -> ObservationToken:
        self._validate_target(target)
        with self._lock:
            started = self._require_finite_clock(self._clock(), name="clock")
            metric = self._get_or_create_metric(target)
            self._next_token += 1
            tracked = len(self._active_tokens) < self._max_active
            if tracked and metric is not None:
                metric.in_flight += 1
                metric.peak_in_flight = max(metric.peak_in_flight, metric.in_flight)
            token = ObservationToken(
                target, started, self._next_token, self._watcher_id, tracked
            )
            if tracked:
                self._active_tokens[token.identifier] = token
        return token

    def finish(
        self,
        token: ObservationToken,
        *,
        outcome: Outcome = "success",
        duration_seconds: float | None = None,
        detail: str | None = None,
    ) -> None:
        duration = (
            self._clock() - token.started
            if duration_seconds is None
            else duration_seconds
        )
        detail_text = self._sanitize_detail(detail)
        with self._lock:
            active_token = self._active_tokens.get(token.identifier)
            if active_token != token:
                if not token.tracked:
                    return
                raise ValueError("observation token was already finished")
            self._validate_duration(duration)
            self._validate_outcome(outcome)
            del self._active_tokens[token.identifier]
            metric = self._get_or_create_metric(token.target)
            if metric is not None:
                metric.in_flight -= 1
                self._record_locked(
                    metric, float(duration), outcome=outcome, detail_text=detail_text
                )

    def record(
        self,
        target: str,
        duration_seconds: float,
        *,
        outcome: Outcome = "success",
        detail: str | None = None,
    ) -> None:
        self._validate_target(target)
        self._validate_duration(duration_seconds)
        self._validate_outcome(outcome)
        detail_text = self._sanitize_detail(detail)
        with self._lock:
            metric = self._get_or_create_metric(target)
            if metric is not None:
                self._record_locked(
                    metric,
                    float(duration_seconds),
                    outcome=outcome,
                    detail_text=detail_text,
                )

    def record_event(self, target: str, event: EventKind) -> None:
        self._validate_target(target)
        if event not in _EVENT_KINDS:
            raise ValueError(f"invalid event: {event}")
        with self._lock:
            metric = self._get_or_create_metric(target)
            if metric is not None:
                metric.events[event] = metric.events.get(event, 0) + 1
                if event in _COUNTED_EVENT_KINDS:
                    setattr(metric, event, getattr(metric, event) + 1)

    def event_count(self, target: str, event: str) -> int:
        with self._lock:
            metric = self._metrics.get(target)
            return metric.events.get(event, 0) if metric is not None else 0

    def event_total(self, prefix: str, event: str) -> int:
        with self._lock:
            return sum(
                metric.events.get(event, 0)
                for target, metric in self._metrics.items()
                if target.startswith(prefix)
            )

    def snapshot(self) -> ObservabilitySnapshot:
        with self._lock:
            session_started_at = self._session_started_at
            metrics = tuple(
                self._snapshot_metric(target, metric)
                for target, metric in sorted(self._metrics.items())
            )
            captured_at = self._require_finite_clock(
                self._wall_clock(), name="wall_clock"
            )
            return ObservabilitySnapshot(
                session_started_at,
                captured_at,
                max(captured_at - session_started_at, 0.0),
                metrics,
            )

    def reset(self) -> None:
        """Start a fresh in-memory observation session."""

        with self._lock:
            new_started_at = self._require_finite_clock(
                self._wall_clock(), name="wall_clock"
            )
            self._metrics.clear()
            self._active_tokens.clear()
            self._session_started_at = new_started_at

    def _record_locked(
        self,
        metric: _Metric,
        duration: float,
        *,
        outcome: Outcome,
        detail_text: str | None,
    ) -> None:
        metric.count += 1
        metric.samples.append(float(duration))
        if outcome == "success":
            metric.successes += 1
        elif outcome == "failure":
            metric.failures += 1
            metric.last_error = detail_text or "failure"
        else:
            metric.cancellations += 1

    def _get_or_create_metric(self, target: str) -> _Metric | None:
        metric = self._metrics.get(target)
        if metric is not None:
            return metric
        if len(self._metrics) >= self._max_targets:
            return None
        metric = _Metric(deque(maxlen=self._sample_limit))
        self._metrics[target] = metric
        return metric

    @staticmethod
    def _sanitize_detail(detail: object) -> str | None:
        if detail is None:
            return None
        text = str(detail)
        return text[:160] if text else None

    @staticmethod
    def _validate_duration(duration_seconds: float) -> None:
        if not math.isfinite(duration_seconds) or duration_seconds < 0:
            raise ValueError("duration_seconds must be finite and non-negative")

    @staticmethod
    def _validate_outcome(outcome: Outcome) -> None:
        if outcome not in ("success", "failure", "cancelled"):
            raise ValueError(f"invalid outcome: {outcome}")

    @staticmethod
    def _validate_target(target: str) -> None:
        if not isinstance(target, str):
            raise ValueError(  # noqa: TRY004 - preserve the ValueError contract.
                "target must be a string"
            )
        if not target.strip():
            raise ValueError("target must be non-empty")
        if len(target) > 160:
            raise ValueError("target must be at most 160 characters")

    @staticmethod
    def _require_finite_clock(value: float, *, name: str) -> float:
        if (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(value)
        ):
            raise ValueError(f"{name} returned a non-finite value")
        return value

    @staticmethod
    def _snapshot_metric(target: str, metric: _Metric) -> MetricSnapshot:
        samples = tuple(metric.samples)
        distribution: Mapping[str, float | int] = (
            FrozenDistribution(summarize_samples(samples))
            if samples
            else FrozenDistribution({})
        )
        return MetricSnapshot(
            target,
            metric.count,
            metric.successes,
            metric.failures,
            metric.cancellations,
            metric.coalesced,
            metric.stale,
            metric.rejected,
            tuple(sorted(metric.events.items())),
            metric.in_flight,
            metric.peak_in_flight,
            samples,
            distribution,
            metric.last_error,
        )

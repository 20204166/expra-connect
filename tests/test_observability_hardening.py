"""Test-first hardening regressions for bounded runtime observability.

Each test here pins an invariant that the pre-hardening implementation either
violated outright or did not guarantee.  The suite is organised by concern:

    * validation (sample_limit, target, clock, statistics)
    * snapshot immutability
    * token safety (forged / stale / cross-watcher)
    * finish atomicity (single lock transition, no half-finish)
    * observation_scope failure isolation
    * resource bounds (target cardinality, detail, event cardinality)
    * concurrency invariants
"""

import math
import threading
import unittest
from typing import Any, cast

from expra_connect.observability import (
    ObservabilityWatcher,
    Outcome,
    observation_scope,
    summarize_samples,
)


class _RecordingObserver:
    """Minimal observer that records the outcome passed to ``finish``."""

    def __init__(self) -> None:
        self.outcome: Outcome | None = None
        self.finish_count = 0

    def begin(self, target: str) -> str:
        return target

    def finish(self, token: str, *, outcome: Outcome = "success") -> None:
        self.finish_count += 1
        self.outcome = outcome


class _FailingObserver:
    """Observer whose ``finish`` raises, for failure-isolation tests."""

    def begin(self, target: str) -> str:
        return target

    def finish(self, token: str, *, outcome: Outcome = "success") -> None:
        raise RuntimeError("observer unavailable")


class _BeginFailingObserver:
    """Observer whose ``begin`` raises, for begin-isolation tests."""

    def begin(self, target: str) -> str:
        raise RuntimeError("observer begin failed")

    def finish(self, token: str, *, outcome: Outcome = "success") -> None:
        raise AssertionError("finish must not be reached")


class ValidationTests(unittest.TestCase):
    def test_sample_limit_rejects_bool_nonint_and_nonpositive(self) -> None:
        for value in (True, False, 1.5, "128", None, 0, -1):
            with self.assertRaises(ValueError, msg=repr(value)):
                ObservabilityWatcher(sample_limit=value)  # type: ignore[arg-type]
        self.assertEqual(ObservabilityWatcher(sample_limit=1)._sample_limit, 1)

    def test_target_rejects_nonstring_and_whitespace(self) -> None:
        watcher = ObservabilityWatcher()
        for value in (None, 123, object()):
            with self.assertRaises(ValueError):
                watcher.record(value, 0.1)  # type: ignore[arg-type]
        for value in ("", "   ", "\t\n"):
            with self.assertRaises(ValueError):
                watcher.record(value, 0.1)
        watcher.record("x" * 160, 0.1)
        with self.assertRaises(ValueError):
            watcher.record("x" * 161, 0.1)

    def test_non_finite_clock_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            ObservabilityWatcher(wall_clock=lambda: math.nan)
        with self.assertRaises(ValueError):
            ObservabilityWatcher(wall_clock=lambda: math.inf)

    def test_non_finite_duration_clock_cannot_create_nan_sample(self) -> None:
        watcher = ObservabilityWatcher()
        watcher._clock = lambda: math.nan
        with self.assertRaises(ValueError):
            watcher.begin("app:scan")

    def test_finish_rejects_nan_and_infinite_durations(self) -> None:
        watcher = ObservabilityWatcher()
        token = watcher.begin("app:scan")
        for duration in (math.nan, math.inf, -math.inf):
            with self.assertRaises(ValueError):
                watcher.finish(token, duration_seconds=duration)
        watcher.finish(token, duration_seconds=0.1)
        self.assertEqual(watcher.snapshot().metrics[0].count, 1)

    def test_summarize_samples_rejects_malformed_direct_input(self) -> None:
        for samples in (
            (),
            (1.0, math.nan),
            (1.0, math.inf),
            (1.0, -math.inf),
            (1.0, 2.0, True),
            (1.0, "2.0"),
        ):
            with self.assertRaises(ValueError, msg=repr(samples)):
                summarize_samples(samples)  # type: ignore[arg-type]

    def test_summarize_samples_rejects_bool_but_accepts_ints(self) -> None:
        summarize_samples((1, 2, 3))
        with self.assertRaises(ValueError):
            summarize_samples((1.0, False))


class StatisticsTests(unittest.TestCase):
    def test_distribution_pins_are_stable(self) -> None:
        distribution = summarize_samples((1.0, 2.0, 3.0, 4.0, 5.0))
        self.assertEqual(distribution["minimum"], 1.0)
        self.assertEqual(distribution["p25"], 2.0)
        self.assertEqual(distribution["p50"], 3.0)
        self.assertEqual(distribution["median"], 3.0)
        self.assertEqual(distribution["p75"], 4.0)
        self.assertAlmostEqual(distribution["p95"], 4.8)
        self.assertAlmostEqual(distribution["p99"], 4.96)
        self.assertEqual(distribution["maximum"], 5.0)
        self.assertEqual(distribution["spread"], 4.0)
        self.assertEqual(distribution["outliers"], 0)

    def test_outliers_are_upper_only(self) -> None:
        distribution = summarize_samples((0.0, 50.0, 50.0, 50.0, 50.0, 100.0))
        self.assertEqual(distribution["outliers"], 1)

    def test_count_is_all_time_but_samples_are_retained_window(self) -> None:
        watcher = ObservabilityWatcher(sample_limit=2)
        watcher.record("app:scan", 1.0)
        watcher.record("app:scan", 2.0)
        watcher.record("app:scan", 100.0)
        metric = watcher.snapshot().metrics[0]
        self.assertEqual(metric.count, 3)
        self.assertEqual(metric.samples, (2.0, 100.0))
        self.assertEqual(metric.distribution["minimum"], 2.0)
        self.assertEqual(metric.distribution["maximum"], 100.0)


class ImmutabilityTests(unittest.TestCase):
    def test_distribution_cannot_be_mutated_through_snapshot(self) -> None:
        watcher = ObservabilityWatcher()
        watcher.record("app:scan", 0.1)
        metric = watcher.snapshot().metrics[0]
        with self.assertRaises(TypeError):
            cast(dict[str, float | int], metric.distribution)["p50"] = 999

    def test_old_snapshot_is_unaffected_by_future_records(self) -> None:
        watcher = ObservabilityWatcher()
        watcher.record("app:scan", 0.1)
        snapshot_a = watcher.snapshot()
        before_samples = snapshot_a.metrics[0].samples
        before_p50 = snapshot_a.metrics[0].distribution["p50"]

        watcher.record("app:scan", 0.9)

        self.assertEqual(snapshot_a.metrics[0].samples, before_samples)
        self.assertEqual(snapshot_a.metrics[0].distribution["p50"], before_p50)
        self.assertEqual(watcher.snapshot().metrics[0].samples, (0.1, 0.9))


class TokenSafetyTests(unittest.TestCase):
    def test_stale_token_after_reset_is_rejected_and_no_aba(self) -> None:
        watcher = ObservabilityWatcher()
        first = watcher.begin("app:scan")
        watcher.reset()
        second = watcher.begin("app:scan")
        self.assertNotEqual(first.identifier, second.identifier)
        with self.assertRaises(ValueError):
            watcher.finish(first, duration_seconds=0.1)
        watcher.finish(second, duration_seconds=0.1)
        self.assertEqual(watcher.snapshot().metrics[0].count, 1)

    def test_cross_watcher_token_cannot_finish_foreign_observation(self) -> None:
        first = ObservabilityWatcher(clock=lambda: 5.0)
        second = ObservabilityWatcher(clock=lambda: 5.0)
        token_a = first.begin("app:scan")
        token_b = second.begin("app:scan")
        self.assertEqual(token_a.target, token_b.target)
        self.assertEqual(token_a.started, token_b.started)
        self.assertEqual(token_a.identifier, token_b.identifier)
        with self.assertRaises(ValueError):
            second.finish(token_a, duration_seconds=0.1)
        second.finish(token_b, duration_seconds=0.1)
        self.assertEqual(second.snapshot().metrics[0].count, 1)
        self.assertEqual(first.snapshot().metrics[0].count, 0)

    def test_forged_started_time_is_rejected(self) -> None:
        watcher = ObservabilityWatcher()
        token = watcher.begin("app:scan")
        forged = type(token)(token.target, token.started + 1.0, token.identifier)
        with self.assertRaises(ValueError):
            watcher.finish(forged, duration_seconds=0.1)
        watcher.finish(token, duration_seconds=0.1)
        self.assertEqual(watcher.snapshot().metrics[0].count, 1)

    def test_invalid_finish_does_not_consume_token(self) -> None:
        watcher = ObservabilityWatcher()
        token = watcher.begin("app:scan")
        with self.assertRaises(ValueError):
            watcher.finish(token, duration_seconds=-0.1)
        with self.assertRaises(ValueError):
            watcher.finish(token, outcome=cast(Outcome, "unknown"))
        with self.assertRaises(ValueError):
            watcher.finish(token, duration_seconds=math.nan)
        watcher.finish(token, duration_seconds=0.1)
        self.assertEqual(watcher.snapshot().metrics[0].count, 1)


class FinishAtomicityTests(unittest.TestCase):
    def test_finish_holds_lock_across_metric_commit(self) -> None:
        watcher = _PausingWatcher()
        token = watcher.begin("app:scan")

        finisher = threading.Thread(
            target=lambda: watcher.finish(token, duration_seconds=0.1)
        )
        finisher.start()
        self.assertTrue(watcher.commit_entered.wait(timeout=5))

        reset_started = threading.Event()
        reset_done: list[bool] = []

        def do_reset() -> None:
            reset_started.set()
            watcher.reset()
            reset_done.append(True)

        resetter = threading.Thread(target=do_reset)
        resetter.start()
        self.assertTrue(reset_started.wait(timeout=5))
        self.assertFalse(reset_done)
        self.assertTrue(resetter.is_alive())

        watcher.allow_commit.set()
        finisher.join(timeout=5)
        resetter.join(timeout=5)
        self.assertEqual(reset_done, [True])
        self.assertEqual(watcher.snapshot().metrics, ())

    def test_concurrent_reset_and_finish_never_cross_contaminate(self) -> None:
        for _ in range(200):
            watcher = ObservabilityWatcher()
            token = watcher.begin("app:scan")
            barrier = threading.Barrier(2)

            def finish_worker(
                barrier: threading.Barrier = barrier,
                watcher: ObservabilityWatcher = watcher,
                token: Any = token,
            ) -> None:
                barrier.wait()
                try:
                    watcher.finish(token, duration_seconds=0.1)
                except ValueError:
                    pass

            thread = threading.Thread(target=finish_worker)
            thread.start()
            barrier.wait()
            watcher.reset()
            thread.join()
            self.assertEqual(watcher.snapshot().metrics, ())


class ScopeFailureIsolationTests(unittest.TestCase):
    def test_success_path_observer_failure_is_contained(self) -> None:
        ran: list[bool] = []
        with observation_scope(cast(Any, _FailingObserver()), "app:scan"):
            ran.append(True)
        self.assertEqual(ran, [True])

    def test_begin_failure_is_contained(self) -> None:
        ran: list[bool] = []
        with observation_scope(cast(Any, _BeginFailingObserver()), "app:scan"):
            ran.append(True)
        self.assertEqual(ran, [True])

    def test_body_exception_is_not_masked_by_observer_failure(self) -> None:
        with (
            self.assertRaises(PrimaryError),
            observation_scope(cast(Any, _FailingObserver()), "app:scan"),
        ):
            raise PrimaryError("boom")

    def test_cancelled_callback_failure_preserves_body_exception(self) -> None:
        def broken_cancelled() -> bool:
            raise RuntimeError("cancel check failed")

        with (
            self.assertRaises(PrimaryError),
            observation_scope(
                cast(Any, _FailingObserver()), "app:scan", cancelled=broken_cancelled
            ),
        ):
            raise PrimaryError("boom")

    def test_cancellation_classification(self) -> None:
        success: Any = _RecordingObserver()
        with observation_scope(success, "app:scan", cancelled=lambda: True):
            pass
        self.assertEqual(success.outcome, "success")

        failed: Any = _RecordingObserver()
        with (
            self.assertRaises(ValueError),
            observation_scope(failed, "app:scan", cancelled=lambda: False),
        ):
            raise ValueError("boom")
        self.assertEqual(failed.outcome, "failure")

        cancelled: Any = _RecordingObserver()
        with (
            self.assertRaises(ValueError),
            observation_scope(cancelled, "app:scan", cancelled=lambda: True),
        ):
            raise ValueError("boom")
        self.assertEqual(cancelled.outcome, "cancelled")

    def test_base_exception_propagates_and_cleans_up(self) -> None:
        observer: Any = _RecordingObserver()
        with (
            self.assertRaises(KeyboardInterrupt),
            observation_scope(observer, "app:scan"),
        ):
            raise KeyboardInterrupt()
        self.assertEqual(observer.finish_count, 1)
        self.assertEqual(observer.outcome, "failure")

    def test_observer_none_is_a_noop(self) -> None:
        with observation_scope(None, "app:scan"):
            pass


class BoundsTests(unittest.TestCase):
    def test_target_cardinality_is_bounded(self) -> None:
        watcher = ObservabilityWatcher(max_targets=2)
        watcher.record("target:a", 0.1)
        watcher.record("target:b", 0.1)
        watcher.record("target:c", 0.1)
        targets = {metric.target for metric in watcher.snapshot().metrics}
        self.assertEqual(targets, {"target:a", "target:b"})

    def test_begin_finish_on_dropped_target_is_consistent(self) -> None:
        watcher = ObservabilityWatcher(max_targets=1)
        kept = watcher.begin("target:a")
        dropped = watcher.begin("target:b")
        watcher.finish(dropped, duration_seconds=0.1)
        watcher.finish(kept, duration_seconds=0.1)
        snapshot = watcher.snapshot()
        self.assertEqual({metric.target for metric in snapshot.metrics}, {"target:a"})
        self.assertEqual(snapshot.metrics[0].in_flight, 0)

    def test_detail_is_bounded(self) -> None:
        watcher = ObservabilityWatcher()
        watcher.record("app:scan", 0.1, outcome="failure", detail="x" * 500)
        self.assertLessEqual(len(watcher.snapshot().metrics[0].last_error or ""), 160)

    def test_malformed_detail_does_not_corrupt_completion(self) -> None:
        watcher = ObservabilityWatcher()
        token = watcher.begin("app:scan")
        watcher.finish(token, duration_seconds=0.1, outcome="failure", detail=12345)  # type: ignore[arg-type]
        metric = watcher.snapshot().metrics[0]
        self.assertEqual(metric.failures, 1)
        self.assertEqual(metric.last_error, "12345")
        self.assertEqual(metric.in_flight, 0)

    def test_event_only_target_has_zero_count(self) -> None:
        watcher = ObservabilityWatcher()
        watcher.record_event("app:scan", "request")
        metric = watcher.snapshot().metrics[0]
        self.assertEqual(metric.count, 0)
        self.assertEqual(metric.successes, 0)
        self.assertIn(("request", 1), metric.events)

    def test_event_count_does_not_create_metrics(self) -> None:
        watcher = ObservabilityWatcher()
        self.assertEqual(watcher.event_count("missing", "request"), 0)
        self.assertEqual(watcher.snapshot().metrics, ())

    def test_invalid_event_is_rejected(self) -> None:
        watcher = ObservabilityWatcher()
        with self.assertRaises(ValueError):
            watcher.record_event("app:scan", cast(Any, "not-an-event"))

    def test_event_query_is_read_only_and_permissive(self) -> None:
        watcher = ObservabilityWatcher()
        watcher.record_event("ui:render:dashboard", "request")
        self.assertEqual(watcher.event_count("ui:render:dashboard", "unknown"), 0)
        self.assertEqual(watcher.event_total("ui:render:", "request"), 1)
        self.assertEqual(watcher.event_total("missing:", "request"), 0)
        self.assertEqual(watcher.event_total("", "request"), 1)
        self.assertEqual(len(watcher.snapshot().metrics), 1)


class ResetTests(unittest.TestCase):
    def test_reset_is_transactional_when_wall_clock_fails(self) -> None:
        watcher = ObservabilityWatcher()
        watcher.record("app:scan", 0.1)
        calls = {"n": 0}

        def flaky_clock() -> float:
            calls["n"] += 1
            if calls["n"] == 1:
                raise RuntimeError("clock failed")
            return 200.0

        watcher._wall_clock = flaky_clock
        with self.assertRaises(RuntimeError):
            watcher.reset()
        self.assertEqual(len(watcher.snapshot().metrics), 1)

    def test_reset_clears_peak_and_starts_fresh_session(self) -> None:
        watcher = ObservabilityWatcher()
        token = watcher.begin("app:scan")
        watcher.finish(token, duration_seconds=0.1)
        self.assertGreater(watcher.snapshot().metrics[0].peak_in_flight, 0)
        watcher.reset()
        self.assertEqual(watcher.snapshot().metrics, ())


class ConcurrencyTests(unittest.TestCase):
    def test_concurrent_begin_finish_counts_are_exact(self) -> None:
        watcher = ObservabilityWatcher(sample_limit=10000)
        workers = 8
        per_worker = 250
        barrier = threading.Barrier(workers)

        def worker() -> None:
            barrier.wait()
            for _ in range(per_worker):
                token = watcher.begin("app:scan")
                watcher.finish(token, outcome="success", duration_seconds=0.01)

        threads = [threading.Thread(target=worker) for _ in range(workers)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        metric = watcher.snapshot().metrics[0]
        self.assertEqual(metric.count, workers * per_worker)
        self.assertEqual(metric.successes, workers * per_worker)
        self.assertEqual(metric.failures + metric.cancellations, 0)
        self.assertEqual(metric.in_flight, 0)

    def test_concurrent_record_event_increments_are_not_lost(self) -> None:
        watcher = ObservabilityWatcher()
        workers = 8
        per_worker = 500
        barrier = threading.Barrier(workers)

        def worker() -> None:
            barrier.wait()
            for _ in range(per_worker):
                watcher.record_event("app:scan", "coalesced")

        threads = [threading.Thread(target=worker) for _ in range(workers)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        metric = watcher.snapshot().metrics[0]
        self.assertEqual(metric.coalesced, workers * per_worker)
        self.assertIn(("coalesced", workers * per_worker), metric.events)

    def test_peak_and_final_in_flight_under_concurrency(self) -> None:
        watcher = ObservabilityWatcher()
        workers = 6
        barrier = threading.Barrier(workers)

        def worker() -> None:
            token = watcher.begin("app:scan")
            barrier.wait()
            watcher.finish(token, outcome="success", duration_seconds=0.01)

        threads = [threading.Thread(target=worker) for _ in range(workers)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        metric = watcher.snapshot().metrics[0]
        self.assertEqual(metric.in_flight, 0)
        self.assertEqual(metric.peak_in_flight, workers)

    def test_snapshot_invariants_hold_under_concurrency(self) -> None:
        watcher = ObservabilityWatcher(sample_limit=10000)
        stop = threading.Event()

        def producer() -> None:
            while not stop.is_set():
                token = watcher.begin("app:scan")
                watcher.finish(token, outcome="success", duration_seconds=0.01)

        producer_thread = threading.Thread(target=producer)
        producer_thread.start()
        try:
            for _ in range(500):
                for metric in watcher.snapshot().metrics:
                    self.assertEqual(
                        metric.count,
                        metric.successes + metric.failures + metric.cancellations,
                    )
                    self.assertGreaterEqual(metric.in_flight, 0)
        finally:
            stop.set()
            producer_thread.join()


class ActiveTokenBoundTests(unittest.TestCase):
    def test_max_active_rejects_bool_float_and_nonpositive(self) -> None:
        for value in (True, False, 1.5, "1024", None, 0, -1):
            with self.assertRaises(ValueError, msg=repr(value)):
                ObservabilityWatcher(max_active=value)  # type: ignore[arg-type]
        self.assertEqual(ObservabilityWatcher(max_active=4)._max_active, 4)

    def test_abandoned_tokens_do_not_grow_unbounded(self) -> None:
        watcher = ObservabilityWatcher(max_active=5)
        for _ in range(10000):
            watcher.begin("app:scan")
        self.assertEqual(len(watcher._active_tokens), 5)

    def test_below_capacity_all_begins_are_tracked(self) -> None:
        watcher = ObservabilityWatcher(max_active=5)
        tokens = [watcher.begin("app:scan") for _ in range(3)]
        self.assertEqual(len(watcher._active_tokens), 3)
        for token in tokens:
            watcher.finish(token, duration_seconds=0.1)
        metric = watcher.snapshot().metrics[0]
        self.assertEqual(metric.count, 3)
        self.assertEqual(metric.in_flight, 0)

    def test_exact_capacity_and_detached_overflow(self) -> None:
        watcher = ObservabilityWatcher(max_active=5)
        tracked = [watcher.begin("app:scan") for _ in range(5)]
        self.assertEqual(len(watcher._active_tokens), 5)

        detached = watcher.begin("app:scan")
        self.assertEqual(len(watcher._active_tokens), 5)
        self.assertEqual(watcher.snapshot().metrics[0].in_flight, 5)

        watcher.finish(detached, duration_seconds=0.1)
        self.assertEqual(watcher.snapshot().metrics[0].count, 0)
        self.assertEqual(watcher.snapshot().metrics[0].in_flight, 5)

        for token in tracked:
            watcher.finish(token, duration_seconds=0.1)
        self.assertEqual(watcher.snapshot().metrics[0].count, 5)
        self.assertEqual(watcher.snapshot().metrics[0].in_flight, 0)

    def test_finishing_one_frees_capacity(self) -> None:
        watcher = ObservabilityWatcher(max_active=5)
        first = [watcher.begin("app:scan") for _ in range(5)]
        watcher.finish(first[0], duration_seconds=0.1)
        self.assertEqual(len(watcher._active_tokens), 4)
        extra = watcher.begin("app:scan")
        self.assertEqual(len(watcher._active_tokens), 5)
        self.assertEqual(watcher.snapshot().metrics[0].in_flight, 5)
        self.assertTrue(extra.tracked)
        watcher.finish(extra, duration_seconds=0.1)

    def test_stale_token_still_raises_at_saturation(self) -> None:
        watcher = ObservabilityWatcher(max_active=2)
        first = watcher.begin("app:scan")
        second = watcher.begin("app:scan")
        watcher.finish(first, duration_seconds=0.1)
        with self.assertRaises(ValueError):
            watcher.finish(first, duration_seconds=0.1)
        watcher.finish(second, duration_seconds=0.1)

    def test_reset_clears_active_tokens_but_not_identifier_generation(self) -> None:
        watcher = ObservabilityWatcher(max_active=5)
        first = watcher.begin("app:scan")
        watcher.reset()
        self.assertEqual(len(watcher._active_tokens), 0)
        second = watcher.begin("app:scan")
        self.assertGreater(second.identifier, first.identifier)
        with self.assertRaises(ValueError):
            watcher.finish(first, duration_seconds=0.1)

    def test_observation_scope_at_saturation_still_runs_body(self) -> None:
        watcher = ObservabilityWatcher(max_active=1)
        watcher.begin("app:scan")
        ran: list[bool] = []
        with observation_scope(watcher, "app:scan"):
            ran.append(True)
        self.assertEqual(ran, [True])


class _PausingWatcher(ObservabilityWatcher):
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.commit_entered = threading.Event()
        self.allow_commit = threading.Event()

    def _record_locked(
        self,
        metric: Any,
        duration: float,
        *,
        outcome: Outcome,
        detail_text: str | None,
    ) -> None:
        self.commit_entered.set()
        self.allow_commit.wait(timeout=5)
        super()._record_locked(
            metric, duration, outcome=outcome, detail_text=detail_text
        )


class PrimaryError(Exception):
    pass


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import math
import tempfile
import tracemalloc
import unittest
from collections.abc import Callable
from pathlib import Path
from typing import cast

from expra_connect.observability import summarize_samples
from scripts.benchmark_support import (
    BenchmarkOutcome,
    MeasurementConfig,
    RunMetadata,
    load_results,
    measure,
    write_results,
)


class MeasurementTests(unittest.TestCase):
    def test_measures_batched_samples_and_canonical_distribution(self) -> None:
        ticks = iter((0, 1_000, 1_000, 3_000, 3_000, 5_000))
        calls = 0

        def operation() -> None:
            nonlocal calls
            calls += 1

        result = measure(
            operation,
            MeasurementConfig(
                warmups=1, repetitions=3, batch_count=2, track_allocations=False
            ),
            clock_ns=lambda: next(ticks),
        )

        self.assertEqual(calls, 8)
        self.assertEqual(result.samples_us, (0.5, 1.0, 1.0))
        self.assertEqual(result.p50, summarize_samples(result.samples_us)["p50"])
        self.assertEqual(result.p95, summarize_samples(result.samples_us)["p95"])
        self.assertEqual(result.ops_per_second, 1_000_000 / result.mean_us)

    def test_rejects_invalid_measurement_configuration(self) -> None:
        with self.assertRaises(ValueError):
            MeasurementConfig(warmups=-1)
        with self.assertRaises(ValueError):
            MeasurementConfig(repetitions=0)
        with self.assertRaises(ValueError):
            MeasurementConfig(batch_count=0)

    def test_allocation_is_separate_and_trace_stops_when_operation_raises(self) -> None:
        if tracemalloc.is_tracing():
            self.skipTest("tracemalloc is already owned by another test")

        result = measure(
            lambda: bytearray(64),
            MeasurementConfig(warmups=0, repetitions=1, track_allocations=True),
            clock_ns=iter((0, 1_000)).__next__,
        )
        self.assertIsNotNone(result.peak_bytes)
        self.assertFalse(tracemalloc.is_tracing())

        def fail() -> None:
            raise RuntimeError("operation failed")

        with self.assertRaisesRegex(RuntimeError, "operation failed"):
            measure(
                fail,
                MeasurementConfig(warmups=0, repetitions=1, track_allocations=True),
                clock_ns=iter((0, 1_000)).__next__,
            )
        self.assertFalse(tracemalloc.is_tracing())

    def test_rejects_non_finite_measurements(self) -> None:
        ticks = iter((0, math.nan))

        with self.assertRaises(ValueError):
            measure(
                lambda: None,
                MeasurementConfig(warmups=0, repetitions=1),
                clock_ns=cast(Callable[[], int], lambda: next(ticks)),
            )


class ResultSchemaTests(unittest.TestCase):
    def test_round_trips_measured_and_unsupported_cases(self) -> None:
        metadata = RunMetadata(
            schema_version=1,
            suite="persistence",
            package_version="2.2.0.3",
            python_version="3.12.3",
            platform="test-platform",
            modules={"expra_connect.persistence": "/tmp/package/persistence.py"},
            config=MeasurementConfig(warmups=0, repetitions=1),
        )
        measured = measure(
            lambda: None,
            metadata.config,
            clock_ns=iter((0, 1_000)).__next__,
        )
        outcomes = (
            BenchmarkOutcome(
                case_id="json-200k-load",
                operation="json_store_load",
                fixture={"shape": "compact_pairs"},
                size=200_000,
                payload_bytes=3_188_915,
                result=measured,
            ),
            BenchmarkOutcome(
                case_id="msgpack-200k-load",
                operation="msgpack_store_load",
                fixture={"shape": "compact_pairs"},
                size=200_000,
                payload_bytes=None,
                unavailable_reason="Unsupported by payload and array limits",
            ),
        )

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "run.json"
            write_results(path, metadata, outcomes)
            loaded = load_results(path)

        self.assertEqual(loaded.metadata, metadata)
        self.assertEqual(loaded.outcomes, outcomes)


if __name__ == "__main__":
    unittest.main()

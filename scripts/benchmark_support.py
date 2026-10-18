"""Shared mechanics for reproducible standalone benchmark scripts."""

from __future__ import annotations

import importlib
import json
import math
import statistics
import time
import tracemalloc
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class MeasurementConfig:
    warmups: int = 1
    repetitions: int = 3
    batch_count: int = 1
    track_allocations: bool = True

    def __post_init__(self) -> None:
        for name, value, minimum in (
            ("warmups", self.warmups, 0),
            ("repetitions", self.repetitions, 1),
            ("batch_count", self.batch_count, 1),
        ):
            if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
                raise ValueError(f"{name} must be an integer >= {minimum}")
        if not isinstance(self.track_allocations, bool):
            raise TypeError("track_allocations must be a bool")


@dataclass(frozen=True)
class MeasurementResult:
    samples_us: tuple[float, ...]
    minimum: float
    p25: float
    p50: float
    p75: float
    p95: float
    p99: float
    maximum: float
    spread: float
    outliers: int
    mean_us: float
    ops_per_second: float | None
    peak_bytes: int | None

    def __post_init__(self) -> None:
        if not self.samples_us:
            raise ValueError("samples_us must not be empty")
        if any(
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(value)
            or value < 0
            for value in self.samples_us
        ):
            raise ValueError("samples_us must contain finite non-negative values")
        if (
            isinstance(self.outliers, bool)
            or not isinstance(self.outliers, int)
            or self.outliers < 0
            or (
                self.peak_bytes is not None
                and (
                    isinstance(self.peak_bytes, bool)
                    or not isinstance(self.peak_bytes, int)
                    or self.peak_bytes < 0
                )
            )
        ):
            raise ValueError("outliers and peak_bytes must be non-negative")
        actual = _summarize(self.samples_us)
        for field_name, expected in actual.items():
            reported = getattr(self, field_name)
            if isinstance(expected, int):
                if reported != expected:
                    raise ValueError(f"{field_name} does not match samples_us")
            elif not math.isclose(reported, expected, rel_tol=1e-12, abs_tol=1e-12):
                raise ValueError(f"{field_name} does not match samples_us")
        expected_mean = statistics.mean(self.samples_us)
        if not math.isclose(self.mean_us, expected_mean, rel_tol=1e-12, abs_tol=1e-12):
            raise ValueError("mean_us does not match samples_us")
        expected_throughput = 1_000_000 / expected_mean if expected_mean else None
        if expected_throughput is None:
            if self.ops_per_second is not None:
                raise ValueError("ops_per_second must be None for zero mean latency")
        elif self.ops_per_second is None or not math.isclose(
            self.ops_per_second, expected_throughput, rel_tol=1e-12
        ):
            raise ValueError("ops_per_second does not match samples_us")


@dataclass(frozen=True)
class BenchmarkOutcome:
    case_id: str
    operation: str
    fixture: Mapping[str, Any]
    size: int
    payload_bytes: int | None
    config: MeasurementConfig = field(default_factory=MeasurementConfig)
    result: MeasurementResult | None = None
    unavailable_reason: str | None = None

    def __post_init__(self) -> None:
        if not self.case_id.strip() or not self.operation.strip():
            raise ValueError("case_id and operation must be non-empty")
        if (
            isinstance(self.size, bool)
            or not isinstance(self.size, int)
            or self.size < 1
        ):
            raise ValueError("size must be a positive integer")
        if self.payload_bytes is not None and (
            isinstance(self.payload_bytes, bool)
            or not isinstance(self.payload_bytes, int)
            or self.payload_bytes < 0
        ):
            raise ValueError("payload_bytes must be a non-negative integer or None")
        if not isinstance(self.config, MeasurementConfig):
            raise TypeError("config must be a MeasurementConfig")
        measured = self.result is not None
        unavailable = isinstance(self.unavailable_reason, str) and bool(
            self.unavailable_reason.strip()
        )
        if measured == unavailable:
            raise ValueError("provide exactly one result or unavailable_reason")
        if (
            self.result is not None
            and len(self.result.samples_us) != self.config.repetitions
        ):
            raise ValueError("result sample count must match config repetitions")
        if (
            self.result is not None
            and (self.result.peak_bytes is not None) != self.config.track_allocations
        ):
            raise ValueError("result allocation data must match config")


@dataclass(frozen=True)
class RunMetadata:
    schema_version: int
    suite: str
    package_version: str
    python_version: str
    platform: str
    modules: Mapping[str, str]
    config: MeasurementConfig

    def __post_init__(self) -> None:
        if self.schema_version != 1:
            raise ValueError("schema_version must be 1")
        for name in ("suite", "package_version", "python_version", "platform"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{name} must be a non-empty string")
        if not self.modules or any(
            not name or not path for name, path in self.modules.items()
        ):
            raise ValueError("modules must contain non-empty names and paths")


@dataclass(frozen=True)
class BenchmarkRun:
    metadata: RunMetadata
    outcomes: tuple[BenchmarkOutcome, ...]


def _percentile(ordered: Sequence[float], fraction: float) -> float:
    position = (len(ordered) - 1) * fraction
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)


def _summarize(samples: tuple[float, ...]) -> dict[str, float | int]:
    ordered = tuple(sorted(samples))
    p25 = _percentile(ordered, 0.25)
    p75 = _percentile(ordered, 0.75)
    return {
        "minimum": ordered[0],
        "p25": p25,
        "p50": statistics.median(ordered),
        "p75": p75,
        "p95": _percentile(ordered, 0.95),
        "p99": _percentile(ordered, 0.99),
        "maximum": ordered[-1],
        "spread": ordered[-1] - ordered[0],
        "outliers": sum(value > p75 + 1.5 * (p75 - p25) for value in ordered),
    }


def _config_to_dict(config: MeasurementConfig) -> dict[str, int | bool]:
    return {
        "warmups": config.warmups,
        "repetitions": config.repetitions,
        "batch_count": config.batch_count,
        "track_allocations": config.track_allocations,
    }


def _config_from_dict(value: object, label: str) -> MeasurementConfig:
    data = _mapping(value, label)
    _exact_keys(
        data,
        {"warmups", "repetitions", "batch_count", "track_allocations"},
        label,
    )
    return MeasurementConfig(**data)


def measure(
    operation: Callable[[], Any],
    config: MeasurementConfig,
    *,
    clock_ns: Callable[[], int] | None = None,
) -> MeasurementResult:
    clock = time.perf_counter_ns if clock_ns is None else clock_ns

    def run_batch() -> None:
        for _ in range(config.batch_count):
            operation()

    for _ in range(config.warmups):
        run_batch()

    samples: list[float] = []
    for _ in range(config.repetitions):
        started = clock()
        run_batch()
        elapsed_ns = clock() - started
        if elapsed_ns < 0:
            raise ValueError("clock returned a negative elapsed duration")
        sample_us = elapsed_ns / config.batch_count / 1_000
        if not math.isfinite(sample_us):
            raise ValueError("clock produced a non-finite duration")
        samples.append(sample_us)

    sample_tuple = tuple(samples)
    summary = _summarize(sample_tuple)
    mean_us = statistics.mean(sample_tuple)
    peak_bytes: int | None = None
    if config.track_allocations:
        if tracemalloc.is_tracing():
            raise RuntimeError("cannot track allocations while tracemalloc is active")
        tracemalloc.start()
        try:
            baseline_bytes, _ = tracemalloc.get_traced_memory()
            tracemalloc.reset_peak()
            operation()
            _, peak = tracemalloc.get_traced_memory()
            peak_bytes = max(0, peak - baseline_bytes)
        finally:
            tracemalloc.stop()

    return MeasurementResult(
        samples_us=sample_tuple,
        minimum=float(summary["minimum"]),
        p25=float(summary["p25"]),
        p50=float(summary["p50"]),
        p75=float(summary["p75"]),
        p95=float(summary["p95"]),
        p99=float(summary["p99"]),
        maximum=float(summary["maximum"]),
        spread=float(summary["spread"]),
        outliers=int(summary["outliers"]),
        mean_us=mean_us,
        ops_per_second=1_000_000 / mean_us if mean_us else None,
        peak_bytes=peak_bytes,
    )


def collect_modules(module_names: Sequence[str]) -> dict[str, str]:
    if not module_names:
        raise ValueError("module_names must not be empty")
    modules: dict[str, str] = {}
    for name in module_names:
        module = importlib.import_module(name)
        module_path = getattr(module, "__file__", None)
        if not isinstance(module_path, str) or not module_path:
            raise ValueError(f"module has no source path: {name}")
        modules[name] = str(Path(module_path).resolve())
    return modules


def write_results(
    path: Path, metadata: RunMetadata, outcomes: Sequence[BenchmarkOutcome]
) -> None:
    case_ids = [outcome.case_id for outcome in outcomes]
    if len(case_ids) != len(set(case_ids)):
        raise ValueError("case_id values must be unique within a run")
    document = {
        "schema_version": 1,
        "metadata": {
            "suite": metadata.suite,
            "package_version": metadata.package_version,
            "python_version": metadata.python_version,
            "platform": metadata.platform,
            "modules": dict(metadata.modules),
            "config": _config_to_dict(metadata.config),
        },
        "outcomes": [_outcome_to_dict(outcome) for outcome in outcomes],
    }
    path.write_text(
        json.dumps(document, allow_nan=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _outcome_to_dict(outcome: BenchmarkOutcome) -> dict[str, Any]:
    document: dict[str, Any] = {
        "case_id": outcome.case_id,
        "operation": outcome.operation,
        "fixture": dict(outcome.fixture),
        "size": outcome.size,
        "payload_bytes": outcome.payload_bytes,
        "config": _config_to_dict(outcome.config),
    }
    if outcome.result is None:
        document.update(
            {"status": "unavailable", "unavailable_reason": outcome.unavailable_reason}
        )
        return document

    result = outcome.result
    document.update(
        {
            "status": "measured",
            "result": {
                "samples_us": list(result.samples_us),
                "minimum": result.minimum,
                "p25": result.p25,
                "p50": result.p50,
                "p75": result.p75,
                "p95": result.p95,
                "p99": result.p99,
                "maximum": result.maximum,
                "spread": result.spread,
                "outliers": result.outliers,
                "mean_us": result.mean_us,
                "ops_per_second": result.ops_per_second,
                "peak_bytes": result.peak_bytes,
            },
        }
    )
    return document


def load_results(path: Path) -> BenchmarkRun:
    root = _mapping(json.loads(path.read_text(encoding="utf-8")), "root")
    _exact_keys(root, {"schema_version", "metadata", "outcomes"}, "root")
    if root["schema_version"] != 1 or isinstance(root["schema_version"], bool):
        raise ValueError("schema_version must be 1")
    metadata_data = _mapping(root["metadata"], "metadata")
    _exact_keys(
        metadata_data,
        {
            "suite",
            "package_version",
            "python_version",
            "platform",
            "modules",
            "config",
        },
        "metadata",
    )
    config = _config_from_dict(metadata_data["config"], "metadata.config")
    modules_data = _mapping(metadata_data["modules"], "metadata.modules")
    modules = {
        _string(name, "module name"): _string(module_path, "module path")
        for name, module_path in modules_data.items()
    }
    metadata = RunMetadata(
        schema_version=1,
        suite=_string(metadata_data["suite"], "metadata.suite"),
        package_version=_string(
            metadata_data["package_version"], "metadata.package_version"
        ),
        python_version=_string(
            metadata_data["python_version"], "metadata.python_version"
        ),
        platform=_string(metadata_data["platform"], "metadata.platform"),
        modules=modules,
        config=config,
    )
    outcomes_data = root["outcomes"]
    if not isinstance(outcomes_data, list):
        raise TypeError("outcomes must be a list")
    outcomes = tuple(_outcome_from_dict(item) for item in outcomes_data)
    if len({outcome.case_id for outcome in outcomes}) != len(outcomes):
        raise ValueError("case_id values must be unique within a run")
    return BenchmarkRun(metadata, outcomes)


def _mapping(value: object, label: str) -> Mapping[str, Any]:
    if not isinstance(value, dict) or any(not isinstance(key, str) for key in value):
        raise TypeError(f"{label} must be an object with string keys")
    return value


def _exact_keys(value: Mapping[str, Any], expected: set[str], label: str) -> None:
    if set(value) != expected:
        raise ValueError(f"{label} has unexpected or missing fields")


def _string(value: object, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be a non-empty string")
    return value


def _optional_nonnegative_int(value: object, label: str) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{label} must be a non-negative integer or null")
    return value


def _nonnegative_number(value: object, label: str) -> float:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(value)
        or value < 0
    ):
        raise ValueError(f"{label} must be a finite non-negative number")
    return float(value)


def _outcome_from_dict(value: object) -> BenchmarkOutcome:
    data = _mapping(value, "outcome")
    status = data.get("status")
    common = {
        "case_id",
        "operation",
        "fixture",
        "size",
        "payload_bytes",
        "config",
        "status",
    }
    if status == "unavailable":
        _exact_keys(data, common | {"unavailable_reason"}, "unavailable outcome")
        return BenchmarkOutcome(
            case_id=_string(data["case_id"], "outcome.case_id"),
            operation=_string(data["operation"], "outcome.operation"),
            fixture=_mapping(data["fixture"], "outcome.fixture"),
            size=_positive_int(data["size"], "outcome.size"),
            payload_bytes=_optional_nonnegative_int(
                data["payload_bytes"], "outcome.payload_bytes"
            ),
            config=_config_from_dict(data["config"], "outcome.config"),
            unavailable_reason=_string(
                data["unavailable_reason"], "outcome.unavailable_reason"
            ),
        )
    if status != "measured":
        raise ValueError("outcome.status must be measured or unavailable")
    _exact_keys(data, common | {"result"}, "measured outcome")
    result_data = _mapping(data["result"], "outcome.result")
    _exact_keys(
        result_data,
        {
            "samples_us",
            "minimum",
            "p25",
            "p50",
            "p75",
            "p95",
            "p99",
            "maximum",
            "spread",
            "outliers",
            "mean_us",
            "ops_per_second",
            "peak_bytes",
        },
        "outcome.result",
    )
    samples_data = result_data["samples_us"]
    if not isinstance(samples_data, list) or not samples_data:
        raise ValueError("outcome.result.samples_us must be a non-empty list")
    samples = tuple(
        _nonnegative_number(sample, "outcome.result.samples_us")
        for sample in samples_data
    )
    ops_per_second = result_data["ops_per_second"]
    if ops_per_second is not None:
        ops_per_second = _nonnegative_number(
            ops_per_second, "outcome.result.ops_per_second"
        )
    result = MeasurementResult(
        samples_us=samples,
        minimum=_nonnegative_number(result_data["minimum"], "result.minimum"),
        p25=_nonnegative_number(result_data["p25"], "result.p25"),
        p50=_nonnegative_number(result_data["p50"], "result.p50"),
        p75=_nonnegative_number(result_data["p75"], "result.p75"),
        p95=_nonnegative_number(result_data["p95"], "result.p95"),
        p99=_nonnegative_number(result_data["p99"], "result.p99"),
        maximum=_nonnegative_number(result_data["maximum"], "result.maximum"),
        spread=_nonnegative_number(result_data["spread"], "result.spread"),
        outliers=_positive_int_or_zero(result_data["outliers"], "result.outliers"),
        mean_us=_nonnegative_number(result_data["mean_us"], "result.mean_us"),
        ops_per_second=ops_per_second,
        peak_bytes=_optional_nonnegative_int(
            result_data["peak_bytes"], "result.peak_bytes"
        ),
    )
    return BenchmarkOutcome(
        case_id=_string(data["case_id"], "outcome.case_id"),
        operation=_string(data["operation"], "outcome.operation"),
        fixture=_mapping(data["fixture"], "outcome.fixture"),
        size=_positive_int(data["size"], "outcome.size"),
        payload_bytes=_optional_nonnegative_int(
            data["payload_bytes"], "outcome.payload_bytes"
        ),
        config=_config_from_dict(data["config"], "outcome.config"),
        result=result,
    )


def _positive_int(value: object, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError(f"{label} must be a positive integer")
    return value


def _positive_int_or_zero(value: object, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{label} must be a non-negative integer")
    return value


def format_result(result: MeasurementResult) -> str:
    throughput = (
        "n/a"
        if result.ops_per_second is None
        else f"{result.ops_per_second:,.0f} ops/s"
    )
    allocation = (
        "n/a" if result.peak_bytes is None else f"{result.peak_bytes:,} bytes peak"
    )
    return (
        f"n={len(result.samples_us)} p50={result.p50:,.3f} us "
        f"p95={result.p95:,.3f} us p99={result.p99:,.3f} us "
        f"mean={result.mean_us:,.3f} us "
        f"range={result.minimum:,.3f}..{result.maximum:,.3f} us "
        f"{throughput} allocation={allocation}"
    )

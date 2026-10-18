"""Generate updateable performance diagrams from the baseline Markdown tables."""

from __future__ import annotations

import argparse
import json
import math
import sys
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from html import escape
from pathlib import Path

from scripts.benchmark_support import BenchmarkOutcome, BenchmarkRun, load_results

FULL_VERSIONS = (
    "1.8.0.0",
    "1.9.0.1",
    "2.0.0.0",
    "2.0.0.2",
    "2.2.0.0",
    "2.2.0.1",
    "2.2.0.3",
)
REFERENCE_VERSION = "1.8.0.0"
FOCUSED_VERSIONS = FULL_VERSIONS[:-1]
SHARED_HARNESS_LABELS = FULL_VERSIONS + (
    "2.2.0.4 native",
    "2.2.0.4 universal",
)
FULL_METRICS = (
    "Identity fingerprint",
    "Identity create",
    "Role lookup",
    "Capability-grant lookup",
    "Role-state decode",
    "Cluster-state round-trip",
    "Session current-check",
    "Resume existing session",
    "Request sign + verify + replay",
    "JSON store save",
    "JSON store load",
    "MessagePack store save",
    "MessagePack store load",
)
FOCUSED_METRICS = (
    "JSON encode only",
    "JSON decode only",
    "JSON store load",
    "JSON store save",
    "MessagePack encode only",
    "MessagePack decode only",
    "MessagePack store load",
    "MessagePack store save",
)


@dataclass(frozen=True, slots=True)
class WorkloadTable:
    versions: tuple[str, ...]
    rows: dict[str, tuple[float | None, ...]]


@dataclass(frozen=True, slots=True)
class BaselineData:
    full_workloads: dict[int, WorkloadTable]
    focused_persistence: dict[int, WorkloadTable]
    unavailable_reasons: dict[tuple[str, int, str, str], str]


def _reject_duplicate_json_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _object(value: object, description: str) -> dict[str, object]:
    if not isinstance(value, dict) or any(not isinstance(key, str) for key in value):
        raise ValueError(f"{description} must be a JSON object with string keys")
    return value


def _string_list(value: object, description: str) -> tuple[str, ...]:
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise ValueError(f"{description} must be a list of strings")
    items = tuple(value)
    if not items or len(items) != len(set(items)):
        raise ValueError(f"{description} must be non-empty and unique")
    return items


def _load_section(
    root: dict[str, object],
    section_name: str,
    sizes: tuple[int, ...],
    versions: tuple[str, ...],
    metrics: tuple[str, ...],
) -> dict[int, WorkloadTable]:
    section = _object(root.get(section_name), section_name)
    expected_sizes = {str(size) for size in sizes}
    if set(section) != expected_sizes:
        raise ValueError(f"{section_name}: expected size keys {sorted(expected_sizes)}")
    tables: dict[int, WorkloadTable] = {}
    for size in sizes:
        size_data = _object(section[str(size)], f"{section_name}.{size}")
        metric_data = _object(
            size_data.get("metrics"), f"{section_name}.{size}.metrics"
        )
        if set(metric_data) != set(metrics):
            raise ValueError(f"{section_name}.{size}: metric rows do not match schema")
        rows: dict[str, tuple[float | None, ...]] = {}
        for metric in metrics:
            cells = metric_data[metric]
            if not isinstance(cells, list) or len(cells) != len(versions):
                raise ValueError(
                    f"{section_name}.{size}.{metric}: expected {len(versions)} cells"
                )
            parsed: list[float | None] = []
            for cell in cells:
                if cell is None:
                    parsed.append(None)
                elif isinstance(cell, (int, float)) and not isinstance(cell, bool):
                    numeric = float(cell)
                    if not math.isfinite(numeric) or numeric < 0:
                        raise ValueError(
                            f"{section_name}.{size}.{metric}: value must be finite and non-negative"
                        )
                    parsed.append(numeric)
                else:
                    raise ValueError(
                        f"{section_name}.{size}.{metric}: invalid numeric cell {cell!r}"
                    )
            rows[metric] = tuple(parsed)
        tables[size] = WorkloadTable(versions=versions, rows=rows)
    return tables


def _load_unavailable_reasons(
    value: object,
    full_tables: dict[int, WorkloadTable],
    focused_tables: dict[int, WorkloadTable],
) -> dict[tuple[str, int, str, str], str]:
    if not isinstance(value, list):
        raise TypeError("unavailable_reasons must be a list")
    sections = {"full_workloads": full_tables, "focused_persistence": focused_tables}
    reasons: dict[tuple[str, int, str, str], str] = {}
    for item in value:
        record = _object(item, "unavailable reason")
        if set(record) != {"section", "size", "metric", "version", "reason"}:
            raise ValueError("unavailable reason has unexpected or missing fields")
        section = record["section"]
        size = record["size"]
        metric = record["metric"]
        version = record["version"]
        reason = record["reason"]
        if (
            not isinstance(section, str)
            or section not in sections
            or not isinstance(size, int)
            or isinstance(size, bool)
        ):
            raise ValueError("unavailable reason has an invalid section or size")
        table = sections[section].get(size)
        if table is None or not isinstance(metric, str) or metric not in table.rows:
            raise ValueError("unavailable reason refers to an unknown metric")
        allowed_versions = table.versions
        if section == "focused_persistence":
            allowed_versions += (FULL_VERSIONS[-1],)
        if not isinstance(version, str) or version not in allowed_versions:
            raise ValueError("unavailable reason refers to an unknown version")
        if not isinstance(reason, str) or not reason.strip():
            raise ValueError("unavailable reason must be a non-empty string")
        key = (section, size, metric, version)
        if key in reasons:
            raise ValueError(f"duplicate unavailable reason: {key!r}")
        if (
            version in table.versions
            and table.rows[metric][table.versions.index(version)] is not None
        ):
            raise ValueError(f"unavailable reason points to a measured cell: {key!r}")
        reasons[key] = reason
    return reasons


def load_measurements(path: Path) -> BaselineData:
    """Load and validate the canonical performance-measurement JSON file."""

    try:
        root = _object(
            json.loads(
                path.read_text(encoding="utf-8"),
                object_pairs_hook=_reject_duplicate_json_keys,
            ),
            str(path),
        )
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError(
            f"cannot read measurement data from {path}: {error}"
        ) from error
    if set(root) != {
        "schema_version",
        "metadata",
        "full_workloads",
        "focused_persistence",
        "unavailable_reasons",
    }:
        raise ValueError("measurement data has unexpected or missing root fields")
    schema_version = root.get("schema_version")
    if (
        not isinstance(schema_version, int)
        or isinstance(schema_version, bool)
        or schema_version != 1
    ):
        raise ValueError("measurement data schema_version must be 1")
    metadata = _object(root.get("metadata"), "metadata")
    expected_metadata_fields = {
        "measured",
        "runtime",
        "platform",
        "full_versions",
        "focused_versions",
        "fixture_notes",
    }
    if set(metadata) != expected_metadata_fields:
        raise ValueError("metadata has unexpected or missing fields")
    for field_name in ("measured", "runtime", "platform"):
        field_value = metadata[field_name]
        if not isinstance(field_value, str) or not field_value.strip():
            raise ValueError(f"metadata.{field_name} must be a non-empty string")
    full_versions = _string_list(
        metadata.get("full_versions"), "metadata.full_versions"
    )
    focused_versions = _string_list(
        metadata.get("focused_versions"), "metadata.focused_versions"
    )
    if full_versions != FULL_VERSIONS or focused_versions != FOCUSED_VERSIONS:
        raise ValueError(
            "measurement data version lists do not match the supported baseline"
        )
    fixture_notes = _object(metadata["fixture_notes"], "metadata.fixture_notes")
    if set(fixture_notes) != {"full_workloads", "focused_persistence"}:
        raise ValueError("metadata.fixture_notes has unexpected or missing sections")
    note_sizes = {
        "full_workloads": (30_000, 100_000, 200_000),
        "focused_persistence": (30_000, 100_000),
    }
    for section_name, sizes in note_sizes.items():
        notes = _object(
            fixture_notes[section_name], f"metadata.fixture_notes.{section_name}"
        )
        if set(notes) != {str(size) for size in sizes}:
            raise ValueError(
                f"metadata.fixture_notes.{section_name} has incomplete sizes"
            )
        if any(
            not isinstance(note, str) or not note.strip() for note in notes.values()
        ):
            raise ValueError(
                f"metadata.fixture_notes.{section_name} values must be non-empty strings"
            )

    full_tables = _load_section(
        root, "full_workloads", (30_000, 100_000, 200_000), full_versions, FULL_METRICS
    )
    focused_tables = _load_section(
        root,
        "focused_persistence",
        (30_000, 100_000),
        focused_versions,
        FOCUSED_METRICS,
    )
    reasons = _load_unavailable_reasons(
        root.get("unavailable_reasons"), full_tables, focused_tables
    )
    for section_name, tables in (
        ("full_workloads", full_tables),
        ("focused_persistence", focused_tables),
    ):
        for size, table in tables.items():
            for metric, values in table.rows.items():
                for version, cell in zip(table.versions, values, strict=True):
                    if (
                        cell is None
                        and (section_name, size, metric, version) not in reasons
                    ):
                        raise ValueError(
                            f"{section_name}.{size}.{metric}.{version} is missing an unavailable reason"
                        )
    return BaselineData(full_tables, focused_tables, reasons)


@dataclass(frozen=True, slots=True)
class HeatmapRow:
    label: str
    values: tuple[float | None, ...]
    missing_reasons: tuple[str | None, ...]
    value_unit: str = "µs"


@dataclass(frozen=True, slots=True)
class MatchedIndexPanel:
    label: str
    case_count: int
    indices: tuple[float, ...]


@dataclass(frozen=True, slots=True)
class CoverageCell:
    measured: int
    total: int
    unavailable_reasons: tuple[tuple[str, int], ...]


@dataclass(frozen=True, slots=True)
class CoverageRow:
    label: str
    cells: tuple[CoverageCell, ...]


SIZE_INDEX_GROUPS = (
    (1_000, "cluster-1k", "cluster state"),
    (30_000, "cluster-large", "cluster state"),
    (100_000, "cluster-large", "cluster state"),
    (200_000, "persistence-200k", "JSON persistence"),
)


def load_shared_harness_runs(results_dir: Path) -> dict[str, tuple[BenchmarkRun, ...]]:
    """Load comparable shared-harness groups and reject provenance drift."""

    groups = {
        "cluster-1k": FULL_VERSIONS,
        "cluster-large": FULL_VERSIONS,
        "cluster-scaling": FULL_VERSIONS,
        "connect": FULL_VERSIONS,
        "persistence-30k": FOCUSED_VERSIONS,
        "persistence-100k": FOCUSED_VERSIONS,
        "persistence-200k": FULL_VERSIONS,
    }
    loaded: dict[str, tuple[BenchmarkRun, ...]] = {}
    for group, versions in groups.items():
        runs = tuple(
            load_results(results_dir / f"{group}-{version}.json")
            for version in versions
        )
        reference = runs[0]
        expected_suite = (
            "cluster_state" if group.startswith("cluster-") else group.split("-")[0]
        )
        for version, run in zip(versions, runs, strict=True):
            if run.metadata.package_version != version:
                raise ValueError(
                    f"{group}: expected package {version}, got {run.metadata.package_version}"
                )
            if run.metadata.suite != expected_suite:
                raise ValueError(f"{group}: expected suite {expected_suite}")
            if (
                run.metadata.suite != reference.metadata.suite
                or run.metadata.python_version != reference.metadata.python_version
                or run.metadata.platform != reference.metadata.platform
                or run.metadata.config != reference.metadata.config
            ):
                raise ValueError(
                    f"{group}: run metadata is not comparable for {version}"
                )
        reference_cases = {outcome.case_id: outcome for outcome in reference.outcomes}
        for version, run in zip(versions, runs, strict=True):
            cases = {outcome.case_id: outcome for outcome in run.outcomes}
            if cases.keys() != reference_cases.keys():
                raise ValueError(f"{group}: operation coverage differs for {version}")
            for case_id, outcome in cases.items():
                reference_outcome = reference_cases[case_id]
                payload_size_mismatch = (
                    outcome.payload_bytes != reference_outcome.payload_bytes
                    and (
                        group != "connect"
                        or (
                            outcome.result is not None
                            and reference_outcome.result is not None
                        )
                    )
                )
                if (
                    outcome.operation != reference_outcome.operation
                    or outcome.fixture != reference_outcome.fixture
                    or outcome.size != reference_outcome.size
                    or payload_size_mismatch
                    or outcome.config != reference_outcome.config
                ):
                    raise ValueError(
                        f"{group}: fixture or config differs for {version}/{case_id}"
                    )
        loaded[group] = runs

    build_pairs = load_build_comparison_runs(
        results_dir.parents[1] / "docs" / "performance-build-comparison-results"
    )
    for group, runs in tuple(loaded.items()):
        suite = "cluster_state" if group.startswith("cluster-") else group.split("-")[0]
        native, universal = build_pairs[suite]
        case_ids = {outcome.case_id for outcome in runs[0].outcomes}
        build_runs = tuple(
            BenchmarkRun(
                run.metadata,
                tuple(
                    outcome for outcome in run.outcomes if outcome.case_id in case_ids
                ),
            )
            for run in (native, universal)
        )
        loaded[group] = runs + build_runs
    return loaded


BUILD_COMPARISON_SUITES = ("cluster_state", "persistence", "connect")
BUILD_COMPARISON_VERSION = "2.2.0.4"


def load_build_comparison_runs(
    results_dir: Path,
) -> dict[str, tuple[BenchmarkRun, BenchmarkRun]]:
    """Load the matched native/universal run pair for each full benchmark suite."""

    pairs: dict[str, tuple[BenchmarkRun, BenchmarkRun]] = {}
    for suite in BUILD_COMPARISON_SUITES:
        pairs[suite] = (
            load_results(results_dir / f"native-{suite}.json"),
            load_results(results_dir / f"universal-{suite}.json"),
        )
    return pairs


def render_build_comparison_diagrams(
    pairs: dict[str, tuple[BenchmarkRun, BenchmarkRun]],
) -> dict[str, str]:
    """Render separate paired-build diagrams without altering historical charts."""

    diagrams: dict[str, str] = {}
    for suite, (native, universal) in pairs.items():
        _validate_build_comparison_pair(suite, native, universal)
        outcomes_by_id = [
            {outcome.case_id: outcome for outcome in run.outcomes}
            for run in (native, universal)
        ]
        outcome_pairs = [
            (outcomes_by_id[0][case_id], outcomes_by_id[1][case_id])
            for case_id in sorted(
                outcomes_by_id[0],
                key=lambda case_id: (
                    outcomes_by_id[0][case_id].size,
                    outcomes_by_id[0][case_id].operation,
                ),
            )
        ]
        diagrams[f"performance-build-comparison-{suite}.svg"] = (
            _render_build_comparison_svg(suite, outcome_pairs)
        )
    return diagrams


def _validate_build_comparison_pair(
    suite: str, native: BenchmarkRun, universal: BenchmarkRun
) -> None:
    for label, run in (("native", native), ("universal", universal)):
        metadata = run.metadata
        if (
            metadata.suite != suite
            or metadata.package_version != BUILD_COMPARISON_VERSION
        ):
            raise ValueError(
                f"{suite}: expected matching {BUILD_COMPARISON_VERSION} {label} run"
            )
    native_metadata, universal_metadata = native.metadata, universal.metadata
    if (
        native_metadata.python_version != universal_metadata.python_version
        or native_metadata.platform != universal_metadata.platform
        or native_metadata.config != universal_metadata.config
    ):
        raise ValueError(f"{suite}: native and universal run metadata differs")
    if not any(
        Path(path).suffix.lower() in (".so", ".pyd")
        for path in native_metadata.modules.values()
    ):
        raise ValueError(f"{suite}: native run has no compiled extension provenance")
    if not universal_metadata.modules or any(
        Path(path).suffix.lower() != ".py"
        for path in universal_metadata.modules.values()
    ):
        raise ValueError(f"{suite}: universal run must use Python source modules")

    native_cases = {outcome.case_id: outcome for outcome in native.outcomes}
    universal_cases = {outcome.case_id: outcome for outcome in universal.outcomes}
    if native_cases.keys() != universal_cases.keys():
        raise ValueError(f"{suite}: fixture or operation coverage differs")
    for case_id, native_outcome in native_cases.items():
        universal_outcome = universal_cases[case_id]
        if (
            native_outcome.operation != universal_outcome.operation
            or native_outcome.fixture != universal_outcome.fixture
            or native_outcome.size != universal_outcome.size
            or native_outcome.payload_bytes != universal_outcome.payload_bytes
            or native_outcome.config != universal_outcome.config
            or (native_outcome.result is None) != (universal_outcome.result is None)
        ):
            raise ValueError(f"{suite}: fixture or operation coverage differs")


def _render_build_comparison_svg(
    suite: str, outcome_pairs: list[tuple[BenchmarkOutcome, BenchmarkOutcome]]
) -> str:
    width = 1500
    row_height = 46
    top = 126
    height = top + row_height * max(1, len(outcome_pairs)) + 28
    plot_left, plot_right = 460, 1050
    measured = [
        outcome.result.p50
        for pair in outcome_pairs
        for outcome in pair
        if outcome.result is not None
    ]
    smallest = min(measured, default=1.0)
    largest = max(measured, default=1.0)
    min_log = math.log10(max(smallest, 1e-9))
    max_log = math.log10(max(largest, smallest * 1.01))
    span = max(max_log - min_log, 1e-9)
    title = {
        "cluster_state": f"{BUILD_COMPARISON_VERSION} native vs universal · cluster state",
        "persistence": f"{BUILD_COMPARISON_VERSION} native vs universal · persistence",
        "connect": f"{BUILD_COMPARISON_VERSION} native vs universal · connect",
    }.get(suite, f"{BUILD_COMPARISON_VERSION} native vs universal · {suite}")
    escaped_title = escape(title)
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}" role="img" aria-label="{escaped_title}">',
        f"<title>{escaped_title}</title>",
        f'<rect width="{width}" height="{height}" fill="#ffffff"/>',
        _text(16, 30, title, size=22, weight="bold"),
    ]
    parts.extend(
        (
            _text(
                18,
                76,
                "Bars use a shared logarithmic scale; right column shows p50 µs and universal/native ratio.",
                size=13,
            ),
            _text(460, 103, "Native Cython", size=12, color="#2166ac"),
            _text(600, 103, "Universal Python", size=12, color="#b2182b"),
            _text(1190, 103, "p50 µs · universal/native", size=12),
        )
    )
    colors = ("#2166ac", "#b2182b")
    for index, (native, universal) in enumerate(outcome_pairs):
        y = top + index * row_height
        fixture_label = f"{native.size:,} · {native.operation}"
        parts.append(_text(18, y + 15, fixture_label, size=11))
        values = (native.result, universal.result)
        if values[0] is None or values[1] is None:
            parts.append(_text(plot_left, y + 15, "not supported", size=11))
            continue
        latencies = (values[0].p50, values[1].p50)
        for side, latency in enumerate(latencies):
            position = (math.log10(max(latency, 1e-9)) - min_log) / span
            bar_width = 8 + position * (plot_right - plot_left - 8)
            bar_y = y + side * 13
            parts.append(
                f'<rect x="{plot_left}" y="{bar_y}" width="{bar_width:.1f}" '
                f'height="9" fill="{colors[side]}"/>'
            )
        ratio = latencies[1] / latencies[0] if latencies[0] else math.inf
        parts.append(
            _text(
                1190,
                y + 12,
                f"{latencies[0]:,.3f} / {latencies[1]:,.3f} · {ratio:.3f}×",
                size=10,
            )
        )
    parts.append("</svg>")
    return "\n".join(parts)


PALETTE = (
    "#2166ac",
    "#67a9cf",
    "#d1e5f0",
    "#f7f7f7",
    "#fddbc7",
    "#ef8a62",
    "#b2182b",
)
VERSION_COLORS = (
    "#0072b2",
    "#e69f00",
    "#009e73",
    "#cc79a7",
    "#56b4e9",
    "#d55e00",
    "#6a51a3",
    "#a6761d",
    "#1b9e77",
)


def render_diagrams() -> dict[str, str]:
    """Render one across-release comparison with a composite and suite detail."""

    shared_results = (
        Path(__file__).resolve().parents[1] / "tools" / "shared-benchmark-results"
    )
    shared_runs = load_shared_harness_runs(shared_results)
    full_workload_index = _full_workload_index_panel(shared_runs)
    panels = _shared_harness_panels(shared_runs)
    panels.insert(
        0,
        (
            "Composite",
            [
                _composite_performance_row(shared_runs),
                _full_workload_index_row(full_workload_index),
            ],
        ),
    )
    return {
        "performance-comparison.svg": _render_heatmap_svg(
            "Across-release performance comparison",
            SHARED_HARNESS_LABELS,
            panels,
            full_workload_index=full_workload_index,
            support_coverage=_full_workload_coverage_rows(shared_runs),
            size_indices=size_specific_indices(shared_runs),
        ),
    }


def size_specific_indices(
    groups: dict[str, tuple[BenchmarkRun, ...]],
) -> tuple[MatchedIndexPanel, ...]:
    """Summarize matched positive p50 ratios separately for each fixture size."""

    panels: list[MatchedIndexPanel] = []
    for size, group_name, suite in SIZE_INDEX_GROUPS:
        runs = groups[group_name]
        case_maps = [
            {
                outcome.case_id: outcome
                for outcome in run.outcomes
                if outcome.size == size
            }
            for run in runs
        ]
        common_ids = set(case_maps[0])
        for cases in case_maps[1:]:
            common_ids.intersection_update(cases)

        vectors: list[tuple[float, ...]] = []
        for case_id in sorted(common_ids):
            values_list: list[float | None] = []
            for cases in case_maps:
                result = cases[case_id].result
                values_list.append(None if result is None else result.p50)
            values = tuple(values_list)
            if all(value is not None and value > 0 for value in values):
                vectors.append(tuple(value for value in values if value is not None))
        if not vectors:
            raise ValueError(f"size index has no common measured cases at {size}")

        indices = tuple(
            math.exp(
                sum(math.log(values[index] / values[0]) for values in vectors)
                / len(vectors)
            )
            for index in range(len(SHARED_HARNESS_LABELS))
        )
        panels.append(
            MatchedIndexPanel(f"{size // 1000}k · {suite}", len(vectors), indices)
        )
    return tuple(panels)


def _composite_performance_row(
    groups: dict[str, tuple[BenchmarkRun, ...]],
) -> HeatmapRow:
    """Combine common measured cases into equally weighted suite indices."""

    suite_groups = {
        "cluster state": ("cluster-1k", "cluster-large"),
        "persistence": ("persistence-200k",),
        "connect": ("connect",),
    }
    suite_values: list[tuple[float, ...]] = []
    for suite, group_names in suite_groups.items():
        case_vectors = _common_measured_case_vectors(groups, group_names)
        if not case_vectors:
            raise ValueError(f"composite score has no common measured {suite} cases")
        reference_values = [values[0] for values in case_vectors]
        suite_values.append(
            tuple(
                math.exp(
                    sum(
                        math.log(values[index] / reference)
                        for values, reference in zip(
                            case_vectors, reference_values, strict=True
                        )
                    )
                    / len(case_vectors)
                )
                for index in range(len(SHARED_HARNESS_LABELS))
            )
        )

    composite = tuple(
        math.exp(
            sum(math.log(suite[index]) for suite in suite_values) / len(suite_values)
        )
        for index in range(len(SHARED_HARNESS_LABELS))
    )
    return HeatmapRow(
        "Composite index · equal-suite geometric mean",
        composite,
        (None,) * len(composite),
        value_unit="index",
    )


def _full_workload_index_panel(
    groups: dict[str, tuple[BenchmarkRun, ...]],
) -> MatchedIndexPanel:
    """Summarize every common measured full-workload case with equal case weight."""

    group_names = ("cluster-1k", "cluster-large", "connect", "persistence-200k")
    vectors = _common_measured_case_vectors(groups, group_names)
    if not vectors:
        raise ValueError("full-workload index has no common measured cases")
    values = tuple(
        math.exp(
            sum(math.log(vector[index] / vector[0]) for vector in vectors)
            / len(vectors)
        )
        for index in range(len(SHARED_HARNESS_LABELS))
    )
    return MatchedIndexPanel("All included suites and sizes", len(vectors), values)


def _full_workload_index_row(panel: MatchedIndexPanel) -> HeatmapRow:
    return HeatmapRow(
        f"Full-workload index · equal-case · {panel.case_count} cases",
        panel.indices,
        (None,) * len(panel.indices),
        value_unit="index",
    )


def _full_workload_coverage_rows(
    groups: dict[str, tuple[BenchmarkRun, ...]],
) -> tuple[CoverageRow, ...]:
    """Summarize measured and unavailable cases for the scored workload scope."""

    group_labels = (
        ("cluster-1k", "Cluster state · 1k"),
        ("cluster-large", "Cluster state · 30k / 100k"),
        ("connect", "Connect operations"),
        ("persistence-200k", "JSON persistence · 200k"),
    )
    rows: list[CoverageRow] = []
    for group_name, label in group_labels:
        runs = groups[group_name]
        cells: list[CoverageCell] = []
        for run in runs:
            measured = sum(
                outcome.result is not None and outcome.result.p50 > 0
                for outcome in run.outcomes
            )
            unavailable = Counter(
                outcome.unavailable_reason or "Unavailable reason not recorded"
                for outcome in run.outcomes
                if outcome.result is None or outcome.result.p50 <= 0
            )
            cells.append(
                CoverageCell(
                    measured,
                    len(run.outcomes),
                    tuple(sorted(unavailable.items())),
                )
            )
        rows.append(CoverageRow(label, tuple(cells)))

    total_cells: list[CoverageCell] = []
    for version_index in range(len(SHARED_HARNESS_LABELS)):
        cells = [row.cells[version_index] for row in rows]
        reasons: Counter[str] = Counter()
        for cell in cells:
            reasons.update(dict(cell.unavailable_reasons))
        total_cells.append(
            CoverageCell(
                sum(cell.measured for cell in cells),
                sum(cell.total for cell in cells),
                tuple(sorted(reasons.items())),
            )
        )
    rows.append(CoverageRow("Total in full-workload scope", tuple(total_cells)))
    return tuple(rows)


def _common_measured_case_vectors(
    groups: dict[str, tuple[BenchmarkRun, ...]], group_names: tuple[str, ...]
) -> list[tuple[float, ...]]:
    """Return positive-p50 case vectors present in every run of selected groups."""

    vectors: list[tuple[float, ...]] = []
    for group_name in group_names:
        runs = groups[group_name]
        case_maps = [
            {outcome.case_id: outcome for outcome in run.outcomes} for run in runs
        ]
        common_ids = set(case_maps[0])
        for cases in case_maps[1:]:
            common_ids.intersection_update(cases)
        for case_id in sorted(common_ids):
            values: list[float] = []
            for cases in case_maps:
                result = cases[case_id].result
                if result is None or result.p50 <= 0:
                    break
                values.append(result.p50)
            else:
                vectors.append(tuple(values))
    return vectors


def _shared_harness_panels(
    groups: dict[str, tuple[BenchmarkRun, ...]],
) -> list[tuple[str, list[HeatmapRow]]]:
    titles = {
        "cluster-1k": "Shared harness · cluster state · 1k entries",
        "cluster-large": "Shared harness · cluster state · 30k / 100k",
        "cluster-scaling": "Shared harness · cluster round-trip scaling · 30k / 100k",
        "connect": "Shared harness · connect · matched operations",
        "persistence-30k": "Shared harness · persistence · 30k dictionary records",
        "persistence-100k": "Shared harness · persistence · 100k compact pairs",
        "persistence-200k": "Shared harness · persistence · 200k compact pairs",
    }
    panels: list[tuple[str, list[HeatmapRow]]] = []
    for group, runs in groups.items():
        first_cases = {outcome.case_id: outcome for outcome in runs[0].outcomes}
        rows: list[HeatmapRow] = []
        for case_id in sorted(first_cases):
            values: list[float | None] = []
            reasons: list[str | None] = []
            outcome = first_cases[case_id]
            for run in runs:
                current = next(item for item in run.outcomes if item.case_id == case_id)
                values.append(None if current.result is None else current.result.p50)
                reasons.append(current.unavailable_reason)
            if len(runs) == len(FOCUSED_VERSIONS) + 2:
                values.insert(len(FOCUSED_VERSIONS), None)
                reasons.insert(len(FOCUSED_VERSIONS), "Not measured for 2.2.0.3")
            if (
                len(runs) < len(SHARED_HARNESS_LABELS)
                and len(runs) != len(FOCUSED_VERSIONS) + 2
            ):
                values.extend([None] * (len(SHARED_HARNESS_LABELS) - len(runs)))
                reasons.extend(
                    ["Not measured"] * (len(SHARED_HARNESS_LABELS) - len(runs))
                )
            label = outcome.operation
            if group == "cluster-scaling":
                label = f"{outcome.size // 1000}k {label}"
            rows.append(HeatmapRow(label, tuple(values), tuple(reasons)))
        panels.append((titles[group], rows))
    return panels


def _rows_from_table(
    baseline: BaselineData,
    section: str,
    size: int,
    table: WorkloadTable,
    metrics: tuple[str, ...],
) -> list[HeatmapRow]:
    return [
        HeatmapRow(
            metric,
            table.rows[metric],
            tuple(
                baseline.unavailable_reasons.get((section, size, metric, version))
                for version in table.versions
            ),
        )
        for metric in metrics
    ]


def _persistence_panels(
    baseline: BaselineData,
) -> list[tuple[str, list[HeatmapRow]]]:
    metric_groups = (
        ("Identity", FULL_METRICS[:2]),
        ("Lookups", FULL_METRICS[2:4]),
        ("State", FULL_METRICS[4:6]),
        ("Sessions", FULL_METRICS[6:8]),
        ("Requests", FULL_METRICS[8:9]),
        ("Persistence", FULL_METRICS[9:]),
    )
    panels: list[tuple[str, list[HeatmapRow]]] = []
    for group_name, metrics in metric_groups:
        full_rows = [
            HeatmapRow(
                f"{size // 1000}k full · {metric}",
                table.rows[metric],
                tuple(
                    baseline.unavailable_reasons.get(
                        ("full_workloads", size, metric, version)
                    )
                    for version in table.versions
                ),
            )
            for size, table in baseline.full_workloads.items()
            for metric in metrics
        ]
        panels.append((f"Full-workload · {group_name}", full_rows))

    focused_rows: list[HeatmapRow] = []
    for size, table in baseline.focused_persistence.items():
        for metric, values in table.rows.items():
            reasons = tuple(
                baseline.unavailable_reasons.get(
                    ("focused_persistence", size, metric, version)
                )
                for version in table.versions
            ) + (
                baseline.unavailable_reasons.get(
                    ("focused_persistence", size, metric, FULL_VERSIONS[-1])
                ),
            )
            focused_rows.append(
                HeatmapRow(
                    f"{size // 1000}k focused · {metric}",
                    values + (None,),
                    reasons,
                )
            )
    panels.append(("Focused persistence operations", focused_rows))
    return panels


def _ratio_bucket(ratio: float) -> int:
    if ratio < 0.5:
        return 0
    if ratio < 0.75:
        return 1
    if ratio < 0.95:
        return 2
    if ratio <= 1.05:
        return 3
    if ratio < 1.5:
        return 4
    if ratio < 2.0:
        return 5
    return 6


def _render_heatmap_svg(
    title: str,
    versions: tuple[str, ...],
    panels: list[tuple[str, list[HeatmapRow]]],
    *,
    full_workload_index: MatchedIndexPanel | None = None,
    support_coverage: tuple[CoverageRow, ...] = (),
    size_indices: tuple[MatchedIndexPanel, ...] = (),
) -> str:
    left, cell_width, row_height = 345, 112, 32
    width = left + cell_width * len(versions) + 32
    heatmap_height = 142 + sum(66 + row_height * len(rows) for _, rows in panels)
    index_panel_count = len(size_indices) + (
        1 if full_workload_index is not None else 0
    )
    index_height = 70 + 220 * index_panel_count if index_panel_count else 0
    coverage_height = 210 if support_coverage else 0
    height = heatmap_height + index_height + coverage_height
    parts = _svg_header(width, height, title)
    reference_index = versions.index(REFERENCE_VERSION)
    y = 72
    for panel_title, rows in panels:
        parts.append(_text(16, y, panel_title, size=18, weight="bold"))
        y += 31
        for index, version in enumerate(versions):
            x = left + index * cell_width + cell_width // 2
            parts.append(_text(x, y, version, size=12, anchor="middle", weight="bold"))
        y += 10
        for row in rows:
            y += row_height
            parts.append(_text(left - 12, y - 9, row.label, size=12, anchor="end"))
            reference_value = row.values[reference_index]
            for index, value in enumerate(row.values):
                x = left + index * cell_width
                cell_y = y - row_height + 1
                if value is None:
                    color = "#e6e6e6"
                    label = "N/A"
                    reason = row.missing_reasons[index] or "Not available"
                    detail = f"{row.label}, {versions[index]}: {reason}"
                    text_color = "#333333"
                else:
                    if reference_value is None or reference_value <= 0:
                        color = "#e6e6e6"
                        label = "N/A"
                        detail = (
                            f"{row.label}, {versions[index]}: {value:,.3f} {row.value_unit}; "
                            f"{REFERENCE_VERSION} reference unavailable"
                        )
                        text_color = "#333333"
                    else:
                        ratio = value / reference_value
                        bucket = _ratio_bucket(ratio)
                        color = PALETTE[bucket]
                        label = f"{ratio:.3g}×"
                        detail = (
                            f"{row.label}, {versions[index]}: {value:,.3f} {row.value_unit}; "
                            f"{ratio:.6g}× versus {REFERENCE_VERSION}"
                        )
                        text_color = (
                            "#ffffff" if bucket in (0, len(PALETTE) - 1) else "#142323"
                        )
                parts.append(
                    f'<rect x="{x}" y="{cell_y}" width="{cell_width - 2}" '
                    f'height="{row_height - 2}" rx="3" fill="{color}">'
                    f"<title>{escape(detail)}</title></rect>"
                )
                parts.append(
                    _text(
                        x + cell_width / 2 - 1,
                        y - 9,
                        label,
                        size=12,
                        anchor="middle",
                        color=text_color,
                    )
                )
        y += 25
    legend_y = heatmap_height - 38
    parts.append(_text(16, legend_y + 15, "Ratio vs 1.8.0.0:", size=12, weight="bold"))
    for index, color in enumerate(PALETTE):
        x = 112 + index * 98
        parts.append(
            f'<rect x="{x}" y="{legend_y}" width="22" height="18" fill="{color}"/>'
        )
        label = (
            "≤0.5×",
            "0.5–0.75×",
            "0.75–0.95×",
            "≈1.0×",
            "1.05–1.5×",
            "1.5–2×",
            ">2×",
        )[index]
        parts.append(_text(x + 28, legend_y + 14, label, size=11))
    if full_workload_index is not None:
        index_top = heatmap_height + 18
        _render_full_workload_index_plot(
            parts, full_workload_index, versions, index_top
        )
        index_top += 250
        if support_coverage:
            _render_support_coverage(parts, support_coverage, versions, index_top)
            index_top += coverage_height
        if size_indices:
            _render_size_index_panels(parts, size_indices, versions, index_top)
    elif size_indices:
        _render_size_index_panels(parts, size_indices, versions, heatmap_height + 18)
    parts.append("</svg>")
    return "\n".join(parts)


def _render_size_index_panels(
    parts: list[str],
    panels: tuple[MatchedIndexPanel, ...],
    versions: tuple[str, ...],
    top: int,
) -> None:
    """Append separate unconnected point panels for each workload size."""

    parts.append(
        _text(16, top, "Size-specific matched-workload index", size=18, weight="bold")
    )
    parts.append(
        _text(
            16,
            top + 21,
            "Geometric mean of matched p50 ratios vs 1.8.0.0; lower is faster. Points are not connected across sizes.",
            size=11,
            color="#454545",
        )
    )
    for panel_index, panel in enumerate(panels):
        panel_top = top + 48 + panel_index * 220
        _render_index_panel(parts, panel, versions, panel_top)


def _render_full_workload_index_plot(
    parts: list[str], panel: MatchedIndexPanel, versions: tuple[str, ...], top: int
) -> None:
    parts.append(_text(16, top, "Full-workload index plot", size=18, weight="bold"))
    parts.append(
        _text(
            16,
            top + 21,
            "Geometric mean of all common positive-p50 cases across included suites and sizes; lower is faster.",
            size=11,
            color="#454545",
        )
    )
    _render_index_panel(parts, panel, versions, top + 48)


def _render_support_coverage(
    parts: list[str],
    rows: tuple[CoverageRow, ...],
    versions: tuple[str, ...],
    top: int,
) -> None:
    parts.append(_text(16, top, "Workload support / coverage", size=18, weight="bold"))
    parts.append(
        _text(
            16,
            top + 21,
            "Measured cases / cases in scope. Unavailable cases are shown here, not assigned invented latency scores.",
            size=11,
            color="#454545",
        )
    )
    parts.append(
        _text(
            16,
            top + 39,
            "Blue: all cases measured · amber: partial coverage · gray: no cases measured",
            size=10,
            color="#454545",
        )
    )
    left, cell_width = 345, 112
    header_y = top + 58
    for index, version in enumerate(versions):
        x = left + index * cell_width + cell_width // 2
        parts.append(
            _text(x, header_y, version, size=10, anchor="middle", weight="bold")
        )
    for row_index, row in enumerate(rows):
        y = top + 68 + row_index * 26
        is_total = row_index == len(rows) - 1
        parts.append(
            _text(
                left - 12,
                y + 15,
                row.label,
                size=11,
                anchor="end",
                weight="bold" if is_total else "normal",
            )
        )
        for version_index, cell in enumerate(row.cells):
            x = left + version_index * cell_width
            if cell.measured == 0:
                color = "#e6e6e6"
            elif cell.measured == cell.total:
                color = "#d1e5f0"
            else:
                color = "#fddbc7"
            reasons = "; ".join(
                f"{count} unavailable: {reason}"
                for reason, count in cell.unavailable_reasons
            )
            detail = (
                f"{row.label}, {versions[version_index]}: "
                f"{cell.measured} of {cell.total} cases measured. "
                f"{reasons or 'All cases measured.'}"
            )
            parts.append(
                f'<rect x="{x}" y="{y}" width="{cell_width - 2}" height="22" '
                f'rx="3" fill="{color}"><title>{escape(detail)}</title></rect>'
            )
            parts.append(
                _text(
                    x + cell_width / 2 - 1,
                    y + 15,
                    f"{cell.measured}/{cell.total}",
                    size=10,
                    anchor="middle",
                    weight="bold" if is_total else "normal",
                )
            )


def _render_index_panel(
    parts: list[str], panel: MatchedIndexPanel, versions: tuple[str, ...], top: int
) -> None:
    plot_left, plot_right = 490, 1070
    axis_min, axis_max = 0.70, 1.15
    parts.append(
        _text(
            16,
            top + 15,
            f"{panel.label} · {panel.case_count} matched cases",
            size=14,
            weight="bold",
        )
    )
    best = min(panel.indices)
    best_versions = [
        version
        for version, value in zip(versions, panel.indices, strict=True)
        if math.isclose(value, best, rel_tol=1e-12)
    ]
    parts.append(
        _text(
            1085,
            top + 15,
            f"lowest observed index: {', '.join(best_versions)} ({best:.3f})",
            size=10,
            color="#454545",
        )
    )
    rows_top = top + 35
    rows_bottom = rows_top + len(versions) * 17
    for tick in (0.7, 0.8, 0.9, 1.0, 1.1):
        x = plot_left + (tick - axis_min) / (axis_max - axis_min) * (
            plot_right - plot_left
        )
        stroke = "#aab2b5" if tick == 1.0 else "#e5e7e8"
        dash = "4 3" if tick == 1.0 else "none"
        parts.append(
            f'<path d="M {x:.1f} {rows_top} V {rows_bottom}" '
            f'stroke="{stroke}" stroke-dasharray="{dash}"/>'
        )
        parts.append(_text(x, rows_top - 3, f"{tick:.1f}", size=9, anchor="middle"))
    for version_index, (version, value) in enumerate(
        zip(versions, panel.indices, strict=True)
    ):
        y = rows_top + 10 + version_index * 17
        x = plot_left + (value - axis_min) / (axis_max - axis_min) * (
            plot_right - plot_left
        )
        color = VERSION_COLORS[version_index]
        point_radius = 5 if math.isclose(value, best, rel_tol=1e-12) else 4
        parts.append(_text(475, y + 4, version, size=10, anchor="end"))
        parts.append(
            f'<circle cx="{x:.1f}" cy="{y}" r="{point_radius}" fill="{color}">'
            f"<title>{escape(version)} · {escape(panel.label)} · "
            f"index {value:.6f}; {panel.case_count} matched cases</title></circle>"
        )
        parts.append(_text(1085, y + 4, f"{value:.3f}", size=10))


def _svg_header(width: int, height: int, title: str) -> list[str]:
    safe_title = escape(title)
    return [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}" role="img" aria-label="{safe_title}">',
        f"<title>{safe_title}</title>",
        f'<rect width="{width}" height="{height}" fill="#ffffff"/>',
        _text(16, 30, title, size=22, weight="bold"),
        _text(
            16,
            51,
            "Cells compare matched workloads with 1.8.0.0; below 1× is faster. Composite weights cluster, persistence, and connect equally.",
            size=12,
            color="#454545",
        ),
    ]


def _text(
    x: float,
    y: float,
    value: str,
    *,
    size: int = 14,
    anchor: str = "start",
    color: str = "#17252a",
    weight: str = "normal",
) -> str:
    return (
        f'<text x="{x:.1f}" y="{y:.1f}" font-family="Arial, sans-serif" '
        f'font-size="{size}px" text-anchor="{anchor}" fill="{color}" '
        f'font-weight="{weight}">{escape(value)}</text>'
    )


def _render_state_scaling_svg(baseline: BaselineData) -> str:
    width, height = 1420, 650
    plot_left, plot_right = 120, 1050
    plot_width = plot_right - plot_left
    sizes = (30_000, 100_000, 200_000)
    metrics = ("Role-state decode", "Cluster-state round-trip")
    parts = _svg_header(width, height, "Role and cluster state scaling")
    parts.append(
        _text(
            18, 74, "p50 latency in microseconds; logarithmic vertical scale", size=13
        )
    )

    for panel_index, metric in enumerate(metrics):
        top = 105 + panel_index * 250
        plot_top, plot_bottom = top + 24, top + 198
        values = [
            baseline.full_workloads[size].rows[metric][version_index]
            for size in sizes
            for version_index in range(len(FULL_VERSIONS))
        ]
        measured = [value for value in values if value is not None and value > 0]
        low_power = math.floor(math.log10(min(measured)))
        high_power = math.ceil(math.log10(max(measured)))
        if high_power == low_power:
            high_power += 1
        parts.append(_text(plot_left, top, metric, size=17, weight="bold"))
        parts.append(_text(18, (plot_top + plot_bottom) / 2, "p50 µs", size=12))

        for power in range(low_power, high_power + 1):
            value = 10**power
            y = _scale_log_y(value, low_power, high_power, plot_top, plot_bottom)
            parts.append(
                f'<path d="M {plot_left} {y:.1f} H {plot_right}" stroke="#dddddd"/>'
            )
            parts.append(
                _text(
                    plot_left - 8,
                    y + 4,
                    f"10^{power}",
                    size=11,
                    anchor="end",
                    color="#555555",
                )
            )
        for point_index, size in enumerate(sizes):
            x = plot_left + point_index * plot_width / (len(sizes) - 1)
            parts.append(
                f'<path d="M {x:.1f} {plot_top} V {plot_bottom}" stroke="#eeeeee"/>'
            )
            parts.append(
                _text(x, plot_bottom + 22, f"{size // 1000}k", size=12, anchor="middle")
            )

        for version_index, version in enumerate(FULL_VERSIONS):
            points: list[tuple[float, float, int, float]] = []
            for point_index, size in enumerate(sizes):
                value = baseline.full_workloads[size].rows[metric][version_index]
                if value is None or value <= 0:
                    continue
                x = plot_left + point_index * plot_width / (len(sizes) - 1)
                y = _scale_log_y(value, low_power, high_power, plot_top, plot_bottom)
                points.append((x, y, size, value))
            path = " ".join(
                ("M" if index == 0 else "L") + f" {x:.1f} {y:.1f}"
                for index, (x, y, _, _) in enumerate(points)
            )
            if path:
                color = VERSION_COLORS[version_index]
                parts.append(
                    f'<path d="{path}" fill="none" stroke="{color}" stroke-width="2.5"/>'
                )
                for x, y, size, value in points:
                    parts.append(
                        f'<circle cx="{x:.1f}" cy="{y:.1f}" r="4" fill="{color}">'
                        f"<title>{escape(version)} · {size:,} entries · {escape(metric)}: {value:,.3f} µs</title></circle>"
                    )

    legend_y = 116
    for index, version in enumerate(FULL_VERSIONS):
        y = legend_y + index * 32
        color = VERSION_COLORS[index]
        parts.append(f'<path d="M 1090 {y} h 28" stroke="{color}" stroke-width="3"/>')
        parts.append(_text(1126, y + 5, version, size=13))
    parts.append("</svg>")
    return "\n".join(parts)


def _scale_log_y(
    value: float, low_power: int, high_power: int, top: float, bottom: float
) -> float:
    fraction = (math.log10(value) - low_power) / (high_power - low_power)
    return bottom - fraction * (bottom - top)


def sync_diagrams(
    diagrams: dict[str, str], output_dir: Path, *, check: bool
) -> tuple[Path, ...]:
    """Write generated diagrams or return paths that differ from their source."""

    obsolete_filenames = (
        "performance-overview.svg",
        "performance-state-scaling.svg",
        "performance-operation-latency.svg",
        "performance-persistence.svg",
        "performance-shared-harness.svg",
        "performance-build-comparison-cluster_state.svg",
        "performance-build-comparison-persistence.svg",
        "performance-build-comparison-connect.svg",
    )
    stale: list[Path] = []
    for filename, content in sorted(diagrams.items()):
        if Path(filename).name != filename:
            raise ValueError(f"diagram filename must be a simple name: {filename!r}")
        path = output_dir / filename
        if check:
            try:
                actual = path.read_text(encoding="utf-8")
            except FileNotFoundError:
                stale.append(path)
            else:
                if actual != content:
                    stale.append(path)
        else:
            output_dir.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")

    for filename in obsolete_filenames:
        path = output_dir / filename
        if not path.exists():
            continue
        if check:
            stale.append(path)
        else:
            path.unlink()
    return tuple(stale)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check",
        action="store_true",
        help="check whether generated SVG files match the canonical measurement JSON",
    )
    args = parser.parse_args(argv)
    repository_root = Path(__file__).resolve().parents[1]
    output_dir = repository_root / "docs" / "images"
    try:
        diagrams = render_diagrams()
        stale = sync_diagrams(diagrams, output_dir, check=args.check)
    except (OSError, TypeError, ValueError) as error:
        print(f"performance diagram update failed: {error}", file=sys.stderr)
        return 2

    if args.check:
        if stale:
            for path in stale:
                print(
                    f"stale or missing diagram: {path.relative_to(repository_root)}",
                    file=sys.stderr,
                )
            return 1
        print("Performance diagrams are current.")
        return 0

    print(
        f"Updated {len(diagrams)} diagrams in {output_dir.relative_to(repository_root)}."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

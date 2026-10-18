"""Generate updateable performance diagrams from the baseline Markdown tables."""

from __future__ import annotations

import argparse
import math
import re
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from html import escape
from pathlib import Path

FULL_VERSIONS = (
    "1.8.0.0",
    "1.9.0.1",
    "2.0.0.0",
    "2.0.0.2",
    "2.2.0.0",
    "2.2.0.1",
    "2.2.0.3",
)
FOCUSED_VERSIONS = FULL_VERSIONS[:-1]
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


@dataclass(frozen=True, slots=True)
class HeatmapRow:
    label: str
    values: tuple[float | None, ...]
    missing_reasons: tuple[str | None, ...]


PALETTE = (
    "#f2f7f7",
    "#d8ebe9",
    "#b5dbd6",
    "#86c5be",
    "#55a99f",
    "#25877f",
    "#075e59",
)
VERSION_COLORS = (
    "#0072b2",
    "#e69f00",
    "#009e73",
    "#cc79a7",
    "#56b4e9",
    "#d55e00",
    "#6a51a3",
)


def render_diagrams(baseline: BaselineData) -> dict[str, str]:
    """Render the all-workload overview and three distinct metric views."""

    versions = baseline.full_workloads[30_000].versions
    overview = [
        (
            f"{size:,} entries",
            _rows_from_table(baseline.full_workloads[size], FULL_METRICS),
        )
        for size in (30_000, 100_000, 200_000)
    ]
    operations = [
        (
            f"{size:,} entries",
            _rows_from_table(
                baseline.full_workloads[size],
                FULL_METRICS[:4] + FULL_METRICS[6:9],
            ),
        )
        for size in (30_000, 100_000, 200_000)
    ]
    persistence = _persistence_panels(baseline)
    return {
        "performance-overview.svg": _render_heatmap_svg(
            "All full-workload metrics", versions, overview
        ),
        "performance-state-scaling.svg": _render_state_scaling_svg(baseline),
        "performance-operation-latency.svg": _render_heatmap_svg(
            "Identity, lookup, session, and request latency", versions, operations
        ),
        "performance-persistence.svg": _render_heatmap_svg(
            "Persistence operations by codec and harness", versions, persistence
        ),
    }


def _rows_from_table(
    table: WorkloadTable, metrics: tuple[str, ...]
) -> list[HeatmapRow]:
    return [
        HeatmapRow(metric, table.rows[metric], (None,) * len(table.versions))
        for metric in metrics
    ]


def _persistence_panels(
    baseline: BaselineData,
) -> list[tuple[str, list[HeatmapRow]]]:
    versions = baseline.full_workloads[30_000].versions
    full_rows: list[HeatmapRow] = []
    for size, table in baseline.full_workloads.items():
        for metric in FULL_METRICS[9:]:
            reasons: tuple[str | None, ...] = (None,) * len(versions)
            if size == 200_000 and metric.startswith("MessagePack"):
                reasons = ("Unsupported by payload and array limits",) * len(versions)
            full_rows.append(
                HeatmapRow(
                    f"{size // 1000}k full · {metric}", table.rows[metric], reasons
                )
            )

    focused_rows: list[HeatmapRow] = []
    for size, table in baseline.focused_persistence.items():
        for metric, values in table.rows.items():
            focused_rows.append(
                HeatmapRow(
                    f"{size // 1000}k focused · {metric}",
                    values + (None,),
                    (None,) * len(table.versions) + ("Not measured for 2.2.0.3",),
                )
            )
    return [
        ("Full-workload store operations", full_rows),
        ("Focused persistence operations", focused_rows),
    ]


def _render_heatmap_svg(
    title: str,
    versions: tuple[str, ...],
    panels: list[tuple[str, list[HeatmapRow]]],
) -> str:
    left, cell_width, row_height = 345, 112, 32
    width = left + cell_width * len(versions) + 32
    height = 88 + sum(42 + row_height * len(rows) for _, rows in panels) + 104
    parts = _svg_header(width, height, title)
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
            measured = [value for value in row.values if value is not None]
            fastest = min(measured) if measured else None
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
                    ratio = value / fastest if fastest not in (None, 0.0) else 1.0
                    bucket = min(len(PALETTE) - 1, int(math.log2(max(ratio, 1.0))))
                    color = PALETTE[bucket]
                    label = f"{ratio:.1f}×"
                    detail = f"{row.label}, {versions[index]}: {value:,.3f} µs; {ratio:.2f}× row minimum"
                    text_color = "#ffffff" if bucket == len(PALETTE) - 1 else "#142323"
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
    legend_y = height - 38
    parts.append(_text(16, legend_y + 15, "Relative p50:", size=12, weight="bold"))
    for index, color in enumerate(PALETTE):
        x = 112 + index * 98
        parts.append(
            f'<rect x="{x}" y="{legend_y}" width="22" height="18" fill="{color}"/>'
        )
        label = (
            "fastest"
            if index == 0
            else ("≥64×" if index == len(PALETTE) - 1 else f"{2**index}×")
        )
        parts.append(_text(x + 28, legend_y + 14, label, size=11))
    parts.append("</svg>")
    return "\n".join(parts)


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
            "Cell labels are relative to the fastest measured p50 in that row; hover for p50 µs.",
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
    return tuple(stale)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check",
        action="store_true",
        help="check whether generated SVG files match the current Markdown tables",
    )
    args = parser.parse_args(argv)
    repository_root = Path(__file__).resolve().parents[1]
    baseline_path = repository_root / "docs" / "native-wheel-performance-baseline.md"
    output_dir = repository_root / "docs" / "images"
    try:
        baseline = parse_baseline(baseline_path.read_text(encoding="utf-8"))
        diagrams = render_diagrams(baseline)
        stale = sync_diagrams(diagrams, output_dir, check=args.check)
    except (OSError, ValueError) as error:
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


def parse_markdown_table(table_text: str) -> WorkloadTable:
    """Parse one Markdown p50 table, retaining unsupported cells as None."""

    lines = [line.strip() for line in table_text.splitlines() if line.strip()]
    if len(lines) < 3:
        raise ValueError("table must contain a header, separator, and data row")

    header = _split_table_row(lines[0])
    separator = _split_table_row(lines[1])
    if len(header) < 2 or len(separator) != len(header):
        raise ValueError("table header and separator have inconsistent columns")
    if any(not re.fullmatch(r":?-{3,}:?", cell) for cell in separator):
        raise ValueError("table separator row is malformed")

    versions = tuple(header[1:])
    if not all(versions) or len(set(versions)) != len(versions):
        raise ValueError("table version headers must be non-empty and unique")

    rows: dict[str, tuple[float | None, ...]] = {}
    for line in lines[2:]:
        cells = _split_table_row(line)
        if len(cells) != len(header):
            raise ValueError("table data row has inconsistent columns")
        label = cells[0]
        if not label:
            raise ValueError("table data row has an empty metric label")
        if label in rows:
            raise ValueError(f"duplicate metric row: {label}")
        rows[label] = tuple(_parse_p50(label, cell) for cell in cells[1:])
    if not rows:
        raise ValueError("table must contain at least one metric row")
    return WorkloadTable(versions=versions, rows=rows)


def _split_table_row(line: str) -> list[str]:
    if not line.startswith("|") or not line.endswith("|"):
        raise ValueError("table row must start and end with a pipe")
    return [cell.strip() for cell in line[1:-1].split("|")]


def _parse_p50(label: str, cell: str) -> float | None:
    if cell.upper() == "N/A":
        return None
    try:
        value = float(cell.replace(",", ""))
    except ValueError as error:
        raise ValueError(f"{label}: invalid p50 value {cell!r}") from error
    if not math.isfinite(value) or value < 0:
        raise ValueError(f"{label}: p50 value must be finite and non-negative")
    return value


def parse_baseline(markdown: str) -> BaselineData:
    """Parse and validate the workload and focused-persistence tables."""

    full_workloads = {
        size: _parse_validated_section(markdown, heading, FULL_VERSIONS, FULL_METRICS)
        for size, heading in (
            (30_000, "## Full representative workload, 30,000 entries"),
            (100_000, "## Full representative workload, 100,000 entries"),
            (200_000, "## Full representative workload, 200,000 entries"),
        )
    }
    focused_persistence = {
        size: _parse_validated_section(
            markdown, heading, FOCUSED_VERSIONS, FOCUSED_METRICS
        )
        for size, heading in (
            (30_000, "### 30,000 dictionary records"),
            (100_000, "### 100,000 compact-pair records"),
        )
    }
    return BaselineData(
        full_workloads=full_workloads,
        focused_persistence=focused_persistence,
    )


def _parse_validated_section(
    markdown: str,
    heading: str,
    versions: tuple[str, ...],
    required_metrics: tuple[str, ...],
) -> WorkloadTable:
    table = parse_markdown_table(_table_after_heading(markdown, heading))
    if table.versions != versions:
        raise ValueError(
            f"{heading}: expected versions {versions!r}, got {table.versions!r}"
        )
    missing = [metric for metric in required_metrics if metric not in table.rows]
    if missing:
        raise ValueError(f"{heading}: missing required metric row: {missing[0]}")
    return table


def _table_after_heading(markdown: str, heading: str) -> str:
    lines = markdown.splitlines()
    matches = [index for index, line in enumerate(lines) if line.strip() == heading]
    if len(matches) != 1:
        raise ValueError(
            f"expected one section headed {heading!r}, found {len(matches)}"
        )

    heading_level = len(heading) - len(heading.lstrip("#"))
    start = matches[0] + 1
    end = len(lines)
    for index in range(start, len(lines)):
        stripped = lines[index].lstrip()
        if stripped.startswith("#"):
            level = len(stripped) - len(stripped.lstrip("#"))
            if level <= heading_level:
                end = index
                break

    table_start = next(
        (index for index in range(start, end) if lines[index].strip().startswith("|")),
        None,
    )
    if table_start is None:
        raise ValueError(f"{heading}: no Markdown table found")
    table_end = table_start
    while table_end < end and lines[table_end].strip().startswith("|"):
        table_end += 1
    return "\n".join(lines[table_start:table_end])


if __name__ == "__main__":
    raise SystemExit(main())

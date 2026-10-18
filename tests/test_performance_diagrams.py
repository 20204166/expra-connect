from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from xml.etree import ElementTree

from scripts.update_performance_diagrams import (
    FOCUSED_METRICS,
    FULL_METRICS,
    parse_baseline,
    parse_markdown_table,
    render_diagrams,
    sync_diagrams,
)

TABLE = """| Workload | 1.8.0.0 | 2.2.0.3 |
|---|---:|---:|
| Identity fingerprint | 4.793 | 3.532 |
| MessagePack store save | N/A | N/A |
"""

ROOT = Path(__file__).resolve().parents[1]
BASELINE_PATH = ROOT / "docs" / "native-wheel-performance-baseline.md"


class MarkdownTableTests(unittest.TestCase):
    def test_parses_numeric_cells_and_explicit_gaps(self) -> None:
        table = parse_markdown_table(TABLE)

        self.assertEqual(table.versions, ("1.8.0.0", "2.2.0.3"))
        self.assertEqual(table.rows["Identity fingerprint"], (4.793, 3.532))
        self.assertEqual(table.rows["MessagePack store save"], (None, None))

    def test_rejects_malformed_numeric_cells(self) -> None:
        with self.assertRaisesRegex(ValueError, "Identity fingerprint"):
            parse_markdown_table(TABLE.replace("4.793", "fast"))


class BaselineTests(unittest.TestCase):
    def test_parses_all_full_workloads_and_persistence_versions(self) -> None:
        source = BASELINE_PATH.read_text(encoding="utf-8")

        baseline = parse_baseline(source)

        self.assertEqual(tuple(baseline.full_workloads), (30_000, 100_000, 200_000))
        expected_versions = (
            "1.8.0.0",
            "1.9.0.1",
            "2.0.0.0",
            "2.0.0.2",
            "2.2.0.0",
            "2.2.0.1",
            "2.2.0.3",
        )
        for table in baseline.full_workloads.values():
            self.assertEqual(table.versions, expected_versions)
        self.assertEqual(
            baseline.full_workloads[200_000].rows["MessagePack store save"],
            (None,) * len(expected_versions),
        )
        self.assertEqual(
            baseline.focused_persistence[30_000].versions, expected_versions[:-1]
        )

    def test_rejects_a_missing_required_full_workload_metric(self) -> None:
        source = BASELINE_PATH.read_text(encoding="utf-8")
        source = source.replace(
            "| Identity fingerprint | 4.793 | 3.341 |",
            "| Removed fingerprint row | 4.793 | 3.341 |",
            1,
        )

        with self.assertRaisesRegex(ValueError, "Identity fingerprint"):
            parse_baseline(source)

    def test_baseline_embeds_each_generated_diagram_and_refresh_command(self) -> None:
        source = BASELINE_PATH.read_text(encoding="utf-8")

        for filename in (
            "performance-overview.svg",
            "performance-state-scaling.svg",
            "performance-operation-latency.svg",
            "performance-persistence.svg",
        ):
            with self.subTest(filename=filename):
                self.assertIn(f"images/{filename}", source)
        self.assertIn(".venv/bin/python scripts/update_performance_diagrams.py", source)


class DiagramTests(unittest.TestCase):
    def test_renders_four_complete_deterministic_svg_diagrams(self) -> None:
        source = BASELINE_PATH.read_text(encoding="utf-8")
        baseline = parse_baseline(source)

        diagrams = render_diagrams(baseline)

        self.assertEqual(
            set(diagrams),
            {
                "performance-overview.svg",
                "performance-state-scaling.svg",
                "performance-operation-latency.svg",
                "performance-persistence.svg",
            },
        )
        for name, svg in diagrams.items():
            with self.subTest(diagram=name):
                root = ElementTree.fromstring(svg)
                self.assertTrue(root.tag.endswith("svg"))
                self.assertEqual(svg, render_diagrams(baseline)[name])
                for version in baseline.full_workloads[30_000].versions:
                    self.assertIn(version, svg)

        self.assertIn("MessagePack store save", diagrams["performance-overview.svg"])
        for metric in FULL_METRICS:
            self.assertIn(metric, diagrams["performance-overview.svg"])
        self.assertIn("Role-state decode", diagrams["performance-state-scaling.svg"])
        self.assertIn(
            "Cluster-state round-trip", diagrams["performance-state-scaling.svg"]
        )
        self.assertIn(
            "Request sign + verify + replay",
            diagrams["performance-operation-latency.svg"],
        )
        for metric in FULL_METRICS[:4] + FULL_METRICS[6:9]:
            self.assertIn(metric, diagrams["performance-operation-latency.svg"])
        for metric in FOCUSED_METRICS:
            self.assertIn(metric, diagrams["performance-persistence.svg"])
        self.assertIn(
            "Not measured for 2.2.0.3", diagrams["performance-persistence.svg"]
        )
        self.assertIn(
            "Unsupported by payload and array limits",
            diagrams["performance-persistence.svg"],
        )

    def test_sync_writes_and_detects_stale_svg_files(self) -> None:
        baseline = parse_baseline(BASELINE_PATH.read_text(encoding="utf-8"))
        diagrams = render_diagrams(baseline)

        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            self.assertEqual(sync_diagrams(diagrams, output, check=False), ())
            self.assertEqual(sync_diagrams(diagrams, output, check=True), ())
            stale_path = output / "performance-overview.svg"
            stale_path.write_text("stale", encoding="utf-8")

            self.assertEqual(sync_diagrams(diagrams, output, check=True), (stale_path,))


if __name__ == "__main__":
    unittest.main()

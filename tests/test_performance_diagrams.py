from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from pathlib import Path
from xml.etree import ElementTree

from scripts.benchmark_support import (
    BenchmarkOutcome,
    BenchmarkRun,
    MeasurementConfig,
    RunMetadata,
    measure,
)
from scripts.update_performance_diagrams import (
    SHARED_HARNESS_LABELS,
    _full_workload_index_panel,
    _full_workload_index_row,
    load_measurements,
    load_shared_harness_runs,
    render_build_comparison_diagrams,
    render_diagrams,
    size_specific_indices,
    sync_diagrams,
)

ROOT = Path(__file__).resolve().parents[1]
BASELINE_PATH = ROOT / "docs" / "native-wheel-performance-baseline.md"
DATA_PATH = ROOT / "docs" / "performance-baseline-data.json"


class BuildComparisonTests(unittest.TestCase):
    def test_shared_harness_chart_includes_both_2204_builds(self) -> None:
        diagrams = render_diagrams()
        self.assertEqual(set(diagrams), {"performance-comparison.svg"})
        svg = diagrams["performance-comparison.svg"]

        self.assertIn("2.2.0.4 native", svg)
        self.assertIn("2.2.0.4 universal", svg)
        self.assertIn("Composite index · equal-suite geometric mean", svg)
        self.assertIn("Full-workload index · equal-case · 37 cases", svg)
        self.assertIn("cluster state", svg)
        self.assertIn("persistence", svg)
        self.assertIn("connect", svg)

    def test_baseline_embeds_only_unified_chart(self) -> None:
        source = BASELINE_PATH.read_text(encoding="utf-8")

        self.assertIn("images/performance-comparison.svg", source)
        for old_chart in (
            "performance-overview.svg",
            "performance-state-scaling.svg",
            "performance-operation-latency.svg",
            "performance-persistence.svg",
            "performance-shared-harness.svg",
            "performance-build-comparison-cluster_state.svg",
            "performance-build-comparison-persistence.svg",
            "performance-build-comparison-connect.svg",
        ):
            self.assertNotIn(old_chart, source)
        self.assertIn("docs/performance-build-comparison-results", source)

    def test_paired_diagrams_compare_native_and_universal_runs(self) -> None:
        config = MeasurementConfig(warmups=1, repetitions=2, track_allocations=False)
        fixture = {"shape": "small", "size": 1}

        def make_run(flavor: str, samples: tuple[int, ...]) -> BenchmarkRun:
            clock_values = [0]
            for sample in samples:
                clock_values.extend((clock_values[-1] + sample * 1_000,) * 2)
            ticks = iter(clock_values)
            result = measure(
                lambda: None,
                config,
                clock_ns=lambda: next(ticks),
            )
            suffix = ".so" if flavor == "native" else ".py"
            return BenchmarkRun(
                RunMetadata(
                    schema_version=1,
                    suite="connect",
                    package_version="2.2.0.4",
                    python_version="3.12.3",
                    platform="Linux test",
                    modules={
                        "expra_connect.remote.wire_protocol": f"/wheel/{flavor}/wire_protocol{suffix}"
                    },
                    config=config,
                ),
                (
                    BenchmarkOutcome(
                        case_id="ping",
                        operation="ping",
                        fixture=fixture,
                        size=1,
                        payload_bytes=16,
                        config=config,
                        result=result,
                    ),
                ),
            )

        diagrams = render_build_comparison_diagrams(
            {
                "connect": (
                    make_run("native", (10, 12)),
                    make_run("universal", (20, 22)),
                )
            }
        )

        self.assertEqual(set(diagrams), {"performance-build-comparison-connect.svg"})
        svg = diagrams["performance-build-comparison-connect.svg"]
        self.assertIn("Native Cython", svg)
        self.assertIn("Universal Python", svg)
        self.assertIn("1.909×", svg)
        self.assertEqual(
            ElementTree.fromstring(svg).attrib["role"],
            "img",
        )

    def test_paired_diagrams_reject_different_fixture_shapes(self) -> None:
        config = MeasurementConfig(warmups=0, repetitions=1, track_allocations=False)
        outcome = BenchmarkOutcome(
            case_id="one",
            operation="ping",
            fixture={"shape": "small"},
            size=1,
            payload_bytes=1,
            config=config,
            result=measure(lambda: None, config, clock_ns=iter((0, 1_000)).__next__),
        )
        native = BenchmarkRun(
            RunMetadata(
                1, "connect", "2.2.0.4", "3.12.3", "Linux", {"m": "/a.so"}, config
            ),
            (outcome,),
        )
        universal = BenchmarkRun(
            RunMetadata(
                1, "connect", "2.2.0.4", "3.12.3", "Linux", {"m": "/b.py"}, config
            ),
            (
                BenchmarkOutcome(
                    case_id="one",
                    operation="ping",
                    fixture={"shape": "different"},
                    size=1,
                    payload_bytes=1,
                    config=config,
                    result=outcome.result,
                ),
            ),
        )

        with self.assertRaisesRegex(ValueError, "fixture or operation coverage"):
            render_build_comparison_diagrams({"connect": (native, universal)})


@unittest.skipUnless(
    DATA_PATH.is_file() and (ROOT / "tools/shared-benchmark-results").is_dir(),
    "local benchmark source artifacts are not present in this checkout",
)
class BaselineTests(unittest.TestCase):
    def test_json_dataset_preserves_historical_values_and_gaps(self) -> None:
        json_baseline = load_measurements(DATA_PATH)

        self.assertEqual(
            json_baseline.full_workloads[200_000].rows["Identity fingerprint"][0],
            4.793,
        )
        self.assertEqual(
            json_baseline.full_workloads[200_000].rows["MessagePack store load"],
            (None,) * 7,
        )
        self.assertEqual(
            json_baseline.unavailable_reasons[
                ("full_workloads", 200_000, "MessagePack store save", "1.8.0.0")
            ],
            "Unsupported by payload and array limits",
        )
        self.assertEqual(
            json_baseline.unavailable_reasons[
                ("focused_persistence", 30_000, "JSON encode only", "2.2.0.3")
            ],
            "Not measured for 2.2.0.3",
        )

    def test_rejects_unknown_json_schema_version(self) -> None:
        dataset = json.loads(DATA_PATH.read_text(encoding="utf-8"))
        dataset["schema_version"] = 2

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "measurements.json"
            path.write_text(json.dumps(dataset), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "schema_version"):
                load_measurements(path)

    def test_rejects_missing_fixture_note(self) -> None:
        dataset = json.loads(DATA_PATH.read_text(encoding="utf-8"))
        del dataset["metadata"]["fixture_notes"]["full_workloads"]["30000"]

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "measurements.json"
            path.write_text(json.dumps(dataset), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "fixture_notes"):
                load_measurements(path)

    def test_parses_all_full_workloads_and_persistence_versions(self) -> None:
        baseline = load_measurements(DATA_PATH)

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
        dataset = json.loads(DATA_PATH.read_text(encoding="utf-8"))
        del dataset["full_workloads"]["30000"]["metrics"]["Identity fingerprint"]

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "measurements.json"
            path.write_text(json.dumps(dataset), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "metric rows"):
                load_measurements(path)

    def test_baseline_embeds_unified_diagram_and_refresh_command(self) -> None:
        source = BASELINE_PATH.read_text(encoding="utf-8")

        for filename in ("performance-comparison.svg",):
            with self.subTest(filename=filename):
                self.assertIn(f"images/{filename}", source)
        self.assertIn(".venv/bin/python -m scripts.update_performance_diagrams", source)

    def test_baseline_explains_local_sources_and_ratios(self) -> None:
        source = BASELINE_PATH.read_text(encoding="utf-8")

        self.assertIn("local `tools/shared-benchmark-results/`", source)
        self.assertIn("geometric mean", source)
        self.assertIn("weighted equally", source)
        self.assertFalse(
            any(line.lstrip().startswith("|") for line in source.splitlines())
        )

    def test_shared_runs_reject_fixture_mismatch(self) -> None:
        source = ROOT / "tools" / "shared-benchmark-results"
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "runs"
            shutil.copytree(source, target)
            run_path = target / "connect-2.0.0.0.json"
            document = json.loads(run_path.read_text(encoding="utf-8"))
            document["outcomes"][0]["fixture"]["shape"] = "other-fixture"
            run_path.write_text(json.dumps(document), encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "fixture or config differs"):
                load_shared_harness_runs(target)

    def test_shared_runs_have_all_versions_and_preserve_approved_gaps(self) -> None:
        runs = load_shared_harness_runs(ROOT / "tools" / "shared-benchmark-results")

        self.assertEqual(
            tuple(run.metadata.package_version for run in runs["cluster-1k"]),
            (
                "1.8.0.0",
                "1.9.0.1",
                "2.0.0.0",
                "2.0.0.2",
                "2.2.0.0",
                "2.2.0.1",
                "2.2.0.3",
                "2.2.0.4",
                "2.2.0.4",
            ),
        )
        for run in runs["cluster-scaling"]:
            self.assertEqual(
                tuple((item.size, item.operation) for item in run.outcomes),
                ((30_000, "cluster round-trip"), (100_000, "cluster round-trip")),
            )
        for run in runs["cluster-large"][:7]:
            operations = {item.operation for item in run.outcomes}
            self.assertEqual(
                operations,
                {
                    "role encode",
                    "role decode",
                    "cluster encode",
                    "cluster decode",
                    "cluster document round-trip",
                    "cluster round-trip",
                    "YAML encode",
                    "YAML parse",
                    "YAML cluster round-trip",
                },
            )
            self.assertTrue(
                all(
                    item.result is None
                    for item in run.outcomes
                    if item.operation.startswith("YAML")
                )
            )
        self.assertFalse(
            (
                ROOT / "tools/shared-benchmark-results/persistence-30k-2.2.0.3.json"
            ).exists()
        )
        self.assertFalse(
            (
                ROOT / "tools/shared-benchmark-results/persistence-100k-2.2.0.3.json"
            ).exists()
        )


class PublishedBaselineTests(unittest.TestCase):
    def test_markdown_embeds_committed_diagrams_without_linking_local_data(
        self,
    ) -> None:
        source = BASELINE_PATH.read_text(encoding="utf-8")
        for filename in ("performance-comparison.svg",):
            with self.subTest(filename=filename):
                self.assertTrue((ROOT / "docs/images" / filename).is_file())
                self.assertIn(f"images/{filename}", source)
        self.assertNotIn("performance-baseline-data.json](", source)
        self.assertNotIn("shared-benchmark-results/](", source)


@unittest.skipUnless(
    DATA_PATH.is_file() and (ROOT / "tools/shared-benchmark-results").is_dir(),
    "local benchmark source artifacts are not present in this checkout",
)
class DiagramTests(unittest.TestCase):
    def test_renders_one_complete_deterministic_svg_diagram(self) -> None:
        diagrams = render_diagrams()

        self.assertEqual(
            set(diagrams),
            {"performance-comparison.svg"},
        )
        for name, svg in diagrams.items():
            with self.subTest(diagram=name):
                root = ElementTree.fromstring(svg)
                self.assertTrue(root.tag.endswith("svg"))
                self.assertEqual(svg, render_diagrams()[name])
                for version in SHARED_HARNESS_LABELS:
                    self.assertIn(version, svg)

        shared_svg = diagrams["performance-comparison.svg"]
        self.assertIn("Not measured for 2.2.0.3", shared_svg)
        self.assertIn("200k compact pairs", shared_svg)
        self.assertIn("MessagePack store", shared_svg)
        shared_runs = load_shared_harness_runs(
            ROOT / "tools" / "shared-benchmark-results"
        )
        first = shared_runs["cluster-1k"][0]
        last = shared_runs["cluster-1k"][-1]
        first_role = next(
            item for item in first.outcomes if item.operation == "role encode"
        )
        last_role = next(
            item for item in last.outcomes if item.operation == "role encode"
        )
        if first_role.result is None or last_role.result is None:
            self.fail("shared cluster role-encode result is unavailable")
        self.assertIn(
            f"{last_role.result.p50 / first_role.result.p50:.3g}×", shared_svg
        )
        self.assertIn(f"{last_role.result.p50:,.3f} µs", shared_svg)

    def test_size_index_panels_use_matched_cases_without_connecting_sizes(self) -> None:
        svg = render_diagrams()["performance-comparison.svg"]
        root = ElementTree.fromstring(svg)
        text = " ".join(
            node.text or "" for node in root.iter() if node.tag.endswith("text")
        )

        for label in (
            "Full-workload index plot",
            "All included suites and sizes · 37 matched cases",
            "Size-specific matched-workload index",
            "1k · cluster state · 9 matched cases",
            "30k · cluster state · 6 matched cases",
            "100k · cluster state · 6 matched cases",
            "200k · JSON persistence · 6 matched cases",
            "lowest observed index",
        ):
            with self.subTest(label=label):
                self.assertIn(label, text)
        self.assertIn("0.764", svg)
        self.assertIn("0.783", svg)
        self.assertIn("0.777", svg)
        self.assertIn("1.000", svg)
        appended = svg[svg.index("Size-specific matched-workload index") :]
        self.assertEqual(appended.count("<circle "), 4 * len(SHARED_HARNESS_LABELS))
        self.assertNotIn(" L ", appended)
        self.assertLess(
            svg.index("Full-workload index plot"),
            svg.index("Size-specific matched-workload index"),
        )
        full_plot = svg[
            svg.index("Full-workload index plot") : svg.index(
                "Size-specific matched-workload index"
            )
        ]
        self.assertEqual(full_plot.count("<circle "), len(SHARED_HARNESS_LABELS))

    def test_size_indices_intersect_only_positive_measured_cases(self) -> None:
        groups = load_shared_harness_runs(ROOT / "tools" / "shared-benchmark-results")

        panels = size_specific_indices(groups)

        self.assertEqual(
            tuple((panel.label, panel.case_count) for panel in panels),
            (
                ("1k · cluster state", 9),
                ("30k · cluster state", 6),
                ("100k · cluster state", 6),
                ("200k · JSON persistence", 6),
            ),
        )
        self.assertAlmostEqual(min(panels[0].indices), 0.7638, places=4)
        self.assertAlmostEqual(min(panels[1].indices), 0.7831, places=4)
        self.assertAlmostEqual(min(panels[2].indices), 0.7771, places=4)
        self.assertAlmostEqual(min(panels[3].indices), 1.0, places=4)
        for panel in panels:
            self.assertEqual(panel.indices[0], 1.0)
            self.assertEqual(len(panel.indices), len(SHARED_HARNESS_LABELS))

    def test_full_workload_index_weights_every_common_case_equally(self) -> None:
        groups = load_shared_harness_runs(ROOT / "tools" / "shared-benchmark-results")

        panel = _full_workload_index_panel(groups)
        row = _full_workload_index_row(panel)

        self.assertEqual(panel.case_count, 37)
        self.assertEqual(panel.label, "All included suites and sizes")
        self.assertEqual(row.label, "Full-workload index · equal-case · 37 cases")
        self.assertEqual(row.values[0], 1.0)
        native_index = row.values[7]
        universal_index = row.values[8]
        if native_index is None or universal_index is None:
            self.fail("full-workload index is unavailable for a 2.2.0.4 build")
        self.assertAlmostEqual(native_index, 0.942746, places=6)
        self.assertAlmostEqual(universal_index, 1.003027, places=6)

    def test_sync_writes_and_detects_stale_svg_files(self) -> None:
        diagrams = render_diagrams()

        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            obsolete = output / "performance-overview.svg"
            obsolete.write_text("obsolete", encoding="utf-8")
            self.assertEqual(sync_diagrams(diagrams, output, check=False), ())
            self.assertFalse(obsolete.exists())
            self.assertEqual(sync_diagrams(diagrams, output, check=True), ())
            stale_path = output / "performance-comparison.svg"
            stale_path.write_text("stale", encoding="utf-8")

            self.assertEqual(sync_diagrams(diagrams, output, check=True), (stale_path,))


if __name__ == "__main__":
    unittest.main()

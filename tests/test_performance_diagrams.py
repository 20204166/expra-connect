from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from xml.etree import ElementTree

import scripts.update_performance_diagrams as performance_diagrams
from scripts.benchmark_support import (
    BenchmarkOutcome,
    BenchmarkRun,
    MeasurementConfig,
    RunMetadata,
    measure,
)
from scripts.update_performance_diagrams import (
    _full_workload_coverage_rows,
    _full_workload_index_panel,
    _full_workload_index_row,
    _render_heatmap_svg,
    _shared_harness_panels,
    _weighted_overall_index_panel,
    load_measurements,
    load_shared_harness_runs,
    render_build_comparison_diagrams,
    render_diagrams,
    render_performance_summary,
    shared_harness_labels,
    size_specific_indices,
    sync_diagrams,
    sync_performance_summary,
)

ROOT = Path(__file__).resolve().parents[1]
BASELINE_PATH = ROOT / "docs" / "native-wheel-performance-baseline.md"
DATA_PATH = ROOT / "docs" / "performance-baseline-data.json"


class BuildComparisonTests(unittest.TestCase):
    @staticmethod
    def _pairwise_run(
        version: str,
        flavor: str,
        suite: str,
        p50: float | None,
        *,
        case_id: str = "shared-case",
        config: MeasurementConfig | None = None,
    ) -> BenchmarkRun:
        run_config = config or MeasurementConfig(
            warmups=0, repetitions=1, track_allocations=False
        )
        result = None
        if p50 is not None:
            elapsed_ns = round(p50 * 1_000)
            ticks = iter(
                value
                for _ in range(run_config.repetitions)
                for value in (0, elapsed_ns)
            )
            result = measure(
                lambda: None,
                run_config,
                clock_ns=ticks.__next__,
            )
        suffix = ".so" if flavor == "native" else ".py"
        return BenchmarkRun(
            RunMetadata(
                1,
                suite,
                version,
                "3.12.3",
                "Linux test",
                {"expra_connect.test.module": f"/wheel/{flavor}/module{suffix}"},
                run_config,
            ),
            (
                BenchmarkOutcome(
                    case_id=case_id,
                    operation=f"{suite} operation",
                    fixture={"shape": "pairwise-test"},
                    size=100_000,
                    payload_bytes=128,
                    config=run_config,
                    result=result,
                    unavailable_reason="unsupported by test fixture"
                    if result is None
                    else None,
                ),
            ),
        )

    @classmethod
    def _pairwise_history(
        cls,
        *,
        latest_config: MeasurementConfig | None = None,
        unsupported: bool = False,
        include_latest_only_case: bool = False,
    ) -> dict[str, tuple[BenchmarkRun, ...]]:
        history: dict[str, tuple[BenchmarkRun, ...]] = {}
        suites = ("cluster_state", "persistence", "connect")
        for suite in suites:
            runs: list[BenchmarkRun] = []
            for version, flavor, p50 in (
                ("2.2.0.4", "native", 100.0),
                ("2.2.0.4", "universal", 120.0),
                ("2.2.0.5", "native", 80.0),
                ("2.2.0.5", "universal", 90.0),
            ):
                case_id = "shared-case"
                case_p50: float | None = p50
                if unsupported and case_id == "shared-case" and version == "2.2.0.5":
                    case_p50 = None
                config = latest_config if version == "2.2.0.5" else None
                runs.append(
                    cls._pairwise_run(
                        version,
                        flavor,
                        suite,
                        case_p50,
                        case_id=case_id,
                        config=config,
                    )
                )
            if include_latest_only_case:
                runs.append(
                    cls._pairwise_run(
                        "2.2.0.5",
                        "native",
                        suite,
                        70.0,
                        case_id="latest-only-case",
                        config=latest_config,
                    )
                )
            # A later one-sided version must not displace the newest complete pair.
            runs.append(cls._pairwise_run("2.2.0.6", "universal", suite, 70.0))
            history[suite] = tuple(runs)
        return history

    def test_latest_pairwise_diagram_compares_newest_complete_versions(self) -> None:
        self.assertTrue(
            hasattr(performance_diagrams, "render_latest_build_pairwise_diagram"),
            "latest-build pairwise renderer has not been implemented",
        )
        svg = performance_diagrams.render_latest_build_pairwise_diagram(
            self._pairwise_history()
        )

        self.assertIn("vs 2.2.0.4 native", svg)
        self.assertIn("vs 2.2.0.5 universal", svg)
        self.assertIn("vs 2.2.0.4 universal", svg)
        self.assertIn("vs 2.2.0.5 native", svg)
        self.assertIn("0.800×", svg)
        self.assertNotIn("2.2.0.6", svg)
        self.assertIn("unweighted", svg)
        self.assertIn("Blue indicates candidate is faster", svg)

    def test_latest_pairwise_diagram_handles_unsupported_and_missing_cases(
        self,
    ) -> None:
        svg = performance_diagrams.render_latest_build_pairwise_diagram(
            self._pairwise_history(unsupported=True, include_latest_only_case=True)
        )

        self.assertIn("shared-case", svg)
        self.assertIn("unsupported: candidate", svg)
        self.assertNotIn("latest-only-case", svg)

    def test_latest_pairwise_diagram_flags_configuration_differences(self) -> None:
        svg = performance_diagrams.render_latest_build_pairwise_diagram(
            self._pairwise_history(
                latest_config=MeasurementConfig(
                    warmups=1, repetitions=3, track_allocations=False
                )
            )
        )

        self.assertIn("Configuration differs", svg)
        self.assertIn("batch_count=1", svg)

    def test_pairwise_ratio_extremes_use_contrasting_text(self) -> None:
        candidate = self._pairwise_run("2.2.0.5", "native", "connect", 40.0).outcomes[0]
        reference = self._pairwise_run("2.2.0.4", "native", "connect", 100.0).outcomes[
            0
        ]

        self.assertEqual(
            performance_diagrams._pairwise_case_cell(candidate, reference),
            ("40.000 / 100.000 µs · 0.400×", "#2166ac", "#ffffff"),
        )

    def test_render_diagrams_includes_pairwise_chart_when_build_history_is_given(
        self,
    ) -> None:
        diagrams = render_diagrams(build_history=self._pairwise_history())

        self.assertEqual(
            set(diagrams),
            {
                "performance-comparison.svg",
                "performance-latest-build-comparison.svg",
            },
        )
        self.assertEqual(
            ElementTree.fromstring(
                diagrams["performance-latest-build-comparison.svg"]
            ).attrib["role"],
            "img",
        )

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

        self.assertIn("Shared-harness JSON is tracked", source)
        self.assertIn("geometric mean", source)
        self.assertIn("receive equal suite weight", source)
        self.assertFalse(
            any(line.lstrip().startswith("|") for line in source.splitlines())
        )

    def test_shared_runs_reject_fixture_mismatch(self) -> None:
        source = ROOT / "tools" / "shared-benchmark-results"
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "runs"
            shutil.copytree(source, target)
            run_path = target / "connect-2.0.0.2.json"
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
                "2.0.0.2",
                "2.2.0.0",
                "2.2.0.1",
                "2.2.0.3",
                "2.2.0.3",
                "2.2.0.4",
                "2.2.0.4",
                "2.2.0.5",
                "2.2.0.5",
            ),
        )
        for run in runs["cluster-scaling"]:
            self.assertEqual(
                tuple((item.size, item.operation) for item in run.outcomes),
                ((30_000, "cluster round-trip"), (100_000, "cluster round-trip")),
            )
        for run in runs["cluster-large"][:4]:
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

    def test_2203_history_is_represented_as_native_and_universal_builds(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            shared = root / "tools" / "shared-benchmark-results"
            shutil.copytree(ROOT / "tools/shared-benchmark-results", shared)
            build = root / "docs" / "performance-build-comparison-results"
            shutil.copytree(ROOT / "docs/performance-build-comparison-results", build)
            for path in shared.glob("*-2.2.0.3.json"):
                path.unlink()
            for suite in ("cluster_state", "persistence", "connect"):
                for flavor in ("native", "universal"):
                    document = json.loads(
                        (build / f"{flavor}-{suite}.json").read_text(encoding="utf-8")
                    )
                    document["metadata"]["package_version"] = "2.2.0.3"
                    output = build / f"2.2.0.3-{flavor}-{suite}.json"
                    output.write_text(json.dumps(document), encoding="utf-8")

            groups = load_shared_harness_runs(shared)
            labels = shared_harness_labels(groups)

        native_index = labels.index("2.2.0.3 native")
        universal_index = labels.index("2.2.0.3 universal")
        self.assertEqual(
            tuple(run.metadata.package_version for run in groups["cluster-1k"])[
                native_index : universal_index + 1
            ],
            ("2.2.0.3", "2.2.0.3"),
        )
        self.assertEqual(
            tuple(len(run.outcomes) for run in groups["persistence-30k"])[
                native_index : universal_index + 1
            ],
            (13, 13),
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
    def test_visible_history_keeps_baseline_and_nine_newest_columns(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            results_dir = root / "tools" / "shared-benchmark-results"
            shutil.copytree(ROOT / "tools/shared-benchmark-results", results_dir)
            build_dir = root / "docs" / "performance-build-comparison-results"
            shutil.copytree(
                ROOT / "docs/performance-build-comparison-results", build_dir
            )
            for suite in ("cluster_state", "persistence", "connect"):
                for flavor in ("native", "universal"):
                    source = build_dir / f"2.2.0.3-{flavor}-{suite}.json"
                    document = json.loads(source.read_text(encoding="utf-8"))
                    document["metadata"]["package_version"] = "2.2.0.6"
                    target = build_dir / f"2.2.0.6-{flavor}-{suite}.json"
                    target.write_text(json.dumps(document), encoding="utf-8")

            groups = load_shared_harness_runs(results_dir)
            labels = shared_harness_labels(groups)
            weighted = _weighted_overall_index_panel(groups)
            summary = render_performance_summary(groups)
            svg = render_diagrams(groups)["performance-comparison.svg"]
            svg_root = ElementTree.fromstring(svg)
            rendered_labels = [
                (node.text, float(node.attrib["y"]))
                for node in svg_root.findall(".//{http://www.w3.org/2000/svg}text")
                if node.text in {labels[0], labels[-1]}
            ]

        self.assertEqual(len(labels), 10)
        self.assertEqual(labels[0], "1.8.0.0")
        self.assertEqual(labels[-2:], ("2.2.0.6 native", "2.2.0.6 universal"))
        self.assertNotIn("1.9.0.1", labels)
        self.assertEqual(len(weighted.indices), 10)
        self.assertNotIn("1.9.0.1:", summary)
        self.assertNotIn("1.9.0.1", svg)
        self.assertIn(labels[-1], svg)
        baseline_y = next(
            y for label, y in reversed(rendered_labels) if label == labels[0]
        )
        newest_y = next(
            y for label, y in reversed(rendered_labels) if label == labels[-1]
        )
        self.assertLess(baseline_y, newest_y)
        self.assertEqual(
            len(groups["cluster-1k"]),
            len(groups["persistence-30k"]),
        )

    def test_evicted_shared_run_json_moves_to_history(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            results_dir = root / "tools" / "shared-benchmark-results"
            shutil.copytree(ROOT / "tools/shared-benchmark-results", results_dir)
            build_dir = root / "docs" / "performance-build-comparison-results"
            shutil.copytree(
                ROOT / "docs/performance-build-comparison-results", build_dir
            )
            groups = load_shared_harness_runs(results_dir)
            evicted_run = groups["cluster-1k"][1]

            archive_evicted_results = getattr(
                performance_diagrams, "_archive_evicted_results", None
            )
            self.assertTrue(callable(archive_evicted_results))
            if not callable(archive_evicted_results):
                return
            archive_evicted_results((evicted_run,), results_dir, build_dir)

            active_files = tuple(results_dir.glob("*-1.9.0.1.json"))
            archived_files = tuple((results_dir / "history").glob("*-1.9.0.1.json"))
            remaining_labels = shared_harness_labels(
                load_shared_harness_runs(results_dir)
            )

        self.assertEqual(active_files, ())
        self.assertEqual(len(archived_files), 7)
        self.assertNotIn("1.9.0.1", remaining_labels)
        self.assertEqual(remaining_labels[0], "1.8.0.0")

    def test_evicted_build_run_archives_only_its_flavor(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            results_dir = root / "tools" / "shared-benchmark-results"
            shutil.copytree(ROOT / "tools/shared-benchmark-results", results_dir)
            build_dir = root / "docs" / "performance-build-comparison-results"
            shutil.copytree(
                ROOT / "docs/performance-build-comparison-results", build_dir
            )
            groups = load_shared_harness_runs(results_dir)
            native_run = groups["cluster-1k"][
                shared_harness_labels(groups).index("2.2.0.3 native")
            ]
            archive_evicted_results = getattr(
                performance_diagrams, "_archive_evicted_results", None
            )
            self.assertTrue(callable(archive_evicted_results))
            if not callable(archive_evicted_results):
                return

            archive_evicted_results((native_run,), results_dir, build_dir)

            native_active = tuple(build_dir.glob("2.2.0.3-native-*.json"))
            native_archived = tuple(
                (build_dir / "history").glob("2.2.0.3-native-*.json")
            )
            universal_active = tuple(build_dir.glob("2.2.0.3-universal-*.json"))
            remaining_labels = shared_harness_labels(
                load_shared_harness_runs(results_dir)
            )

        self.assertEqual(native_active, ())
        self.assertEqual(len(native_archived), 3)
        self.assertEqual(len(universal_active), 3)
        self.assertNotIn("2.2.0.3 native", remaining_labels)
        self.assertIn("2.2.0.3 universal", remaining_labels)

    def test_evicted_legacy_build_flavor_can_be_archived_independently(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            results_dir = root / "tools" / "shared-benchmark-results"
            shutil.copytree(ROOT / "tools/shared-benchmark-results", results_dir)
            build_dir = root / "docs" / "performance-build-comparison-results"
            shutil.copytree(
                ROOT / "docs/performance-build-comparison-results", build_dir
            )
            groups = load_shared_harness_runs(results_dir)
            native_run = groups["cluster-1k"][
                shared_harness_labels(groups).index("2.2.0.4 native")
            ]
            archive_evicted_results = getattr(
                performance_diagrams, "_archive_evicted_results", None
            )
            self.assertTrue(callable(archive_evicted_results))
            if not callable(archive_evicted_results):
                return

            archive_evicted_results((native_run,), results_dir, build_dir)

            remaining_labels = shared_harness_labels(
                load_shared_harness_runs(results_dir)
            )
            archived = tuple((build_dir / "history").glob("native-*.json"))

        self.assertEqual(len(archived), 3)
        self.assertNotIn("2.2.0.4 native", remaining_labels)
        self.assertIn("2.2.0.4 universal", remaining_labels)

    def test_comparison_diagram_viewbox_contains_rendered_content(self) -> None:
        svg = render_diagrams()["performance-comparison.svg"]
        root = ElementTree.fromstring(svg)
        viewbox = tuple(float(value) for value in root.attrib["viewBox"].split())
        height = viewbox[3]

        for text in root.findall(".//{http://www.w3.org/2000/svg}text"):
            baseline = float(text.attrib["y"])
            font_size = float(text.attrib["font-size"].removesuffix("px"))
            self.assertLessEqual(baseline + font_size * 0.25, height)

        for circle in root.findall(".//{http://www.w3.org/2000/svg}circle"):
            self.assertLessEqual(
                float(circle.attrib["cy"]) + float(circle.attrib["r"]), height
            )

    def test_discovers_new_versioned_build_runs_and_appends_chart_columns(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            results_dir = root / "tools" / "shared-benchmark-results"
            shutil.copytree(ROOT / "tools/shared-benchmark-results", results_dir)
            build_dir = root / "docs" / "performance-build-comparison-results"
            shutil.copytree(
                ROOT / "docs/performance-build-comparison-results", build_dir
            )
            for suite in ("cluster_state", "persistence", "connect"):
                for flavor in ("native",):
                    document = json.loads(
                        (build_dir / f"{flavor}-{suite}.json").read_text(
                            encoding="utf-8"
                        )
                    )
                    document["metadata"]["package_version"] = "2.2.0.6"
                    document["metadata"]["modules"] = {
                        name: f"/wheel/{flavor}/{Path(module).name}"
                        for name, module in document["metadata"]["modules"].items()
                    }
                    if suite == "cluster_state":
                        extra_case = dict(document["outcomes"][0])
                        extra_case["case_id"] = "1000:future cluster operation"
                        extra_case["operation"] = "future cluster operation"
                        document["outcomes"].append(extra_case)
                    (build_dir / f"2.2.0.6-{flavor}-{suite}.json").write_text(
                        json.dumps(document), encoding="utf-8"
                    )

            groups = load_shared_harness_runs(results_dir)
            labels = shared_harness_labels(groups)
            svg = _render_heatmap_svg(
                "dynamic release history",
                labels,
                _shared_harness_panels(groups),
                weighted_overall_index=_weighted_overall_index_panel(groups),
                support_coverage=_full_workload_coverage_rows(groups),
            )
            coverage = _full_workload_coverage_rows(groups)

        self.assertEqual(labels[-1], "2.2.0.6 native")
        self.assertEqual(len(groups["cluster-1k"][0].outcomes), 9)
        self.assertEqual(len(groups["cluster-1k"]), len(labels))
        self.assertIn("2.2.0.6 native", svg)
        self.assertIn("future cluster operation", svg)
        self.assertEqual(
            (coverage[0].cells[-1].measured, coverage[0].cells[-1].total), (10, 10)
        )
        ElementTree.fromstring(svg)

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
                for version in shared_harness_labels(
                    load_shared_harness_runs(ROOT / "tools/shared-benchmark-results")
                ):
                    self.assertIn(version, svg)

        shared_svg = diagrams["performance-comparison.svg"]
        self.assertIn("2.2.0.3 native", shared_svg)
        self.assertIn("2.2.0.3 universal", shared_svg)
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
        labels = shared_harness_labels(
            load_shared_harness_runs(ROOT / "tools/shared-benchmark-results")
        )
        self.assertEqual(appended.count("<circle "), 4 * len(labels))
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
        self.assertEqual(full_plot.count("<circle "), len(labels))

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
        native_index = shared_harness_labels(groups).index("2.2.0.4 native")
        self.assertAlmostEqual(panels[0].indices[native_index], 0.7638, places=4)
        self.assertAlmostEqual(panels[1].indices[native_index], 0.7831, places=4)
        self.assertAlmostEqual(panels[2].indices[native_index], 0.7771, places=4)
        self.assertAlmostEqual(panels[3].indices[native_index], 1.103909, places=6)
        for panel in panels:
            self.assertEqual(panel.indices[0], 1.0)
            self.assertEqual(len(panel.indices), len(groups["cluster-1k"]))

    def test_full_workload_index_weights_every_common_case_equally(self) -> None:
        groups = load_shared_harness_runs(ROOT / "tools" / "shared-benchmark-results")

        panel = _full_workload_index_panel(groups)
        row = _full_workload_index_row(panel)

        self.assertEqual(panel.case_count, 37)
        self.assertEqual(panel.label, "All included suites and sizes")
        self.assertEqual(row.label, "Full-workload index · equal-case · 37 cases")
        self.assertEqual(row.values[0], 1.0)
        labels = shared_harness_labels(groups)
        native_index = row.values[labels.index("2.2.0.4 native")]
        universal_index = row.values[labels.index("2.2.0.4 universal")]
        if native_index is None or universal_index is None:
            self.fail("full-workload index is unavailable for a 2.2.0.4 build")
        self.assertAlmostEqual(native_index, 0.942746, places=6)
        self.assertAlmostEqual(universal_index, 1.003027, places=6)

    def test_weighted_overall_index_uses_declared_size_biased_categories(self) -> None:
        groups = load_shared_harness_runs(ROOT / "tools" / "shared-benchmark-results")

        panel = _weighted_overall_index_panel(groups)
        svg = render_diagrams()["performance-comparison.svg"]

        self.assertEqual(panel.case_count, 37)
        self.assertAlmostEqual(panel.indices[0], 1.0)
        labels = shared_harness_labels(groups)
        self.assertAlmostEqual(
            panel.indices[labels.index("2.2.0.4 native")], 0.9744, places=4
        )
        self.assertAlmostEqual(
            panel.indices[labels.index("2.2.0.4 universal")], 0.9919, places=4
        )
        self.assertIn("Weighted overall index", svg)
        self.assertIn("1k cluster: 5%", svg)
        self.assertIn("30k cluster: 15%", svg)
        self.assertIn("100k cluster: 25%", svg)
        self.assertIn("Connect: 20%", svg)
        self.assertIn("200k JSON persistence: 35%", svg)

    def test_generated_summary_reports_current_winner_and_all_unique_coverage(
        self,
    ) -> None:
        groups = load_shared_harness_runs(ROOT / "tools" / "shared-benchmark-results")

        summary = render_performance_summary(groups)

        self.assertTrue(summary.startswith("- **Current weighted winner:**"))
        self.assertIn("\n- **Distinct-workload coverage:**", summary)
        self.assertIn("2.2.0.3 native:", summary)
        self.assertIn("2.2.0.3 universal:", summary)
        self.assertIn("37 matched positive-p50 cases", summary)
        self.assertIn("74 unique cases", summary)
        self.assertIn("2.2.0.4 native: 68/74", summary)

    def test_sync_performance_summary_updates_only_the_marked_region(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "baseline.md"
            path.write_text(
                "before\n<!-- performance-summary:start -->\nold\n"
                "<!-- performance-summary:end -->\nafter\n",
                encoding="utf-8",
            )

            stale = sync_performance_summary(path, "new summary", check=True)
            self.assertTrue(stale)
            self.assertFalse(sync_performance_summary(path, "new summary", check=False))
            self.assertFalse(sync_performance_summary(path, "new summary", check=True))
            self.assertEqual(
                path.read_text(encoding="utf-8"),
                "before\n<!-- performance-summary:start -->\nnew summary\n"
                "<!-- performance-summary:end -->\nafter\n",
            )

    def test_full_workload_coverage_shows_support_outside_scored_cases(self) -> None:
        groups = load_shared_harness_runs(ROOT / "tools" / "shared-benchmark-results")

        rows = _full_workload_coverage_rows(groups)
        svg = render_diagrams()["performance-comparison.svg"]

        self.assertEqual(
            tuple(row.label for row in rows),
            (
                "Cluster state · 1k",
                "Cluster state · 30k",
                "Cluster state · 100k",
                "Connect operations",
                "Persistence · 30k",
                "Persistence · 100k",
                "JSON persistence · 200k",
                "Total unique measured cases",
            ),
        )
        labels = shared_harness_labels(groups)
        native_index = labels.index("2.2.0.3 native")
        universal_index = labels.index("2.2.0.3 universal")
        self.assertEqual(
            (rows[1].cells[native_index].measured, rows[1].cells[native_index].total),
            (9, 9),
        )
        self.assertEqual(
            (
                rows[1].cells[universal_index].measured,
                rows[1].cells[universal_index].total,
            ),
            (9, 9),
        )
        self.assertEqual(
            (rows[2].cells[native_index].measured, rows[2].cells[native_index].total),
            (6, 9),
        )
        self.assertEqual(
            (
                rows[2].cells[universal_index].measured,
                rows[2].cells[universal_index].total,
            ),
            (6, 9),
        )
        self.assertEqual((rows[3].cells[0].measured, rows[3].cells[0].total), (10, 13))
        self.assertEqual(
            (rows[4].cells[native_index].measured, rows[4].cells[native_index].total),
            (13, 13),
        )
        self.assertEqual(
            (
                rows[4].cells[universal_index].measured,
                rows[4].cells[universal_index].total,
            ),
            (13, 13),
        )
        self.assertEqual(
            (rows[-1].cells[native_index].measured, rows[-1].cells[native_index].total),
            (69, 74),
        )
        self.assertEqual(
            (
                rows[-1].cells[universal_index].measured,
                rows[-1].cells[universal_index].total,
            ),
            (69, 74),
        )
        self.assertIn("Workload support / coverage", svg)
        self.assertIn("6/9", svg)
        self.assertIn("69/74", svg)
        self.assertIn("Exceeds configured YAML maximum size 30,000", svg)
        self.assertNotIn(
            "Cluster round-trip scaling",
            svg[svg.index("Workload support / coverage") :],
        )

    def test_coverage_uses_current_native_and_universal_workload_inventory(
        self,
    ) -> None:
        groups = load_shared_harness_runs(ROOT / "tools" / "shared-benchmark-results")
        cluster_runs = list(groups["cluster-1k"])
        old_case = replace(cluster_runs[0].outcomes[0], case_id="historical-only-case")
        cluster_runs[0] = replace(
            cluster_runs[0], outcomes=cluster_runs[0].outcomes + (old_case,)
        )
        for index in (-2, -1):
            run = cluster_runs[index]
            current_case = replace(run.outcomes[0], case_id="current-only-case")
            cluster_runs[index] = replace(run, outcomes=run.outcomes + (current_case,))
        groups["cluster-1k"] = tuple(cluster_runs)

        rows = _full_workload_coverage_rows(groups)

        self.assertEqual((rows[0].cells[0].measured, rows[0].cells[0].total), (9, 10))
        self.assertEqual(
            (rows[0].cells[-2].measured, rows[0].cells[-2].total), (10, 10)
        )
        self.assertEqual(
            (rows[0].cells[-1].measured, rows[0].cells[-1].total), (10, 10)
        )

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

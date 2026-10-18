from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from pathlib import Path
from xml.etree import ElementTree

from scripts.update_performance_diagrams import (
    FOCUSED_METRICS,
    FULL_METRICS,
    load_measurements,
    load_shared_harness_runs,
    render_diagrams,
    sync_diagrams,
)

ROOT = Path(__file__).resolve().parents[1]
BASELINE_PATH = ROOT / "docs" / "native-wheel-performance-baseline.md"
DATA_PATH = ROOT / "docs" / "performance-baseline-data.json"


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
        self.assertIn(".venv/bin/python -m scripts.update_performance_diagrams", source)

    def test_baseline_uses_json_source_and_explains_baseline_ratios(self) -> None:
        source = BASELINE_PATH.read_text(encoding="utf-8")

        self.assertIn("performance-baseline-data.json", source)
        self.assertIn("candidate p50 divided by the matching 1.8.0.0 p50", source)
        self.assertIn("0.7× means 30% lower", source)
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
            ),
        )
        for run in runs["cluster-scaling"]:
            self.assertEqual(
                tuple((item.size, item.operation) for item in run.outcomes),
                ((30_000, "cluster round-trip"), (100_000, "cluster round-trip")),
            )
        for run in runs["cluster-large"]:
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


class DiagramTests(unittest.TestCase):
    def test_renders_five_complete_deterministic_svg_diagrams(self) -> None:
        baseline = load_measurements(DATA_PATH)

        diagrams = render_diagrams(baseline)

        self.assertEqual(
            set(diagrams),
            {
                "performance-overview.svg",
                "performance-state-scaling.svg",
                "performance-operation-latency.svg",
                "performance-persistence.svg",
                "performance-shared-harness.svg",
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
            self.assertIn(metric, diagrams["performance-persistence.svg"])
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
        exact_ratio = f"{10_635.092 / 11_852.171:.3g}×"
        self.assertIn(exact_ratio, diagrams["performance-persistence.svg"])
        self.assertIn("1×", diagrams["performance-persistence.svg"])
        self.assertIn(
            "10,635.092 µs",
            diagrams["performance-persistence.svg"],
        )
        self.assertIn(
            f"{10_635.092 / 11_852.171:.6g}× versus 1.8.0.0",
            diagrams["performance-persistence.svg"],
        )
        shared_svg = diagrams["performance-shared-harness.svg"]
        self.assertIn("Not measured for 2.2.0.3", shared_svg)
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

    def test_sync_writes_and_detects_stale_svg_files(self) -> None:
        baseline = load_measurements(DATA_PATH)
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

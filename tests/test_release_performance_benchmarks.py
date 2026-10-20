from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from scripts.benchmark_release_wheels import (
    run_release_benchmarks,
    run_release_supplement_benchmarks,
)
from scripts.benchmark_support import (
    BenchmarkOutcome,
    MeasurementConfig,
    RunMetadata,
    load_results,
    measure,
    write_results,
)


class ReleasePerformanceTests(unittest.TestCase):
    def test_supplements_are_paired_separate_and_immutable(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            wheels = root / "wheels"
            wheels.mkdir()
            native_wheel = wheels / "expra_connect-3.4.5.6-cp312-cp312-linux_x86_64.whl"
            universal_wheel = wheels / "expra_connect-3.4.5.6-py3-none-any.whl"
            for wheel, native in ((native_wheel, True), (universal_wheel, False)):
                with zipfile.ZipFile(wheel, "w") as archive:
                    archive.writestr(
                        "expra_connect/__init__.py", "__version__ = '3.4.5.6'\n"
                    )
                    archive.writestr("expra_connect/persistence.py", "# module\n")
                    if native:
                        archive.writestr("expra_connect/native.so", b"native")
            results = root / "results"
            results.mkdir()
            original = results / "3.4.5.6-native-connect.json"
            original.write_bytes(b"immutable original")
            before = original.read_bytes()
            config = MeasurementConfig(
                warmups=0, repetitions=1, track_allocations=False
            )
            suite_cases = {
                "cluster_state": (
                    "1000:role authorization",
                    "30000:role authorization",
                    "100000:role authorization",
                ),
                "persistence": tuple(
                    f"{size}:ProfileMigration:Profile migration" for size in (1, 16, 64)
                ),
                "connect": (
                    "discovery candidate validation",
                    "discovery preferred endpoint",
                    "discovery registry add + expire",
                    "pairing relationship projection",
                    "pairing trusted-candidate classification",
                    "pairing next-generation proof classification",
                    "capability authorized dispatch",
                    "surface peer-granted dispatch",
                    "surface cluster-granted dispatch",
                ),
            }
            calls: list[tuple[str, ...]] = []

            def fake_subprocess(
                command: tuple[str, ...], *, cwd: Path, env: dict[str, str], check: bool
            ) -> None:
                calls.append(command)
                script_name = Path(command[1]).name
                suite = {
                    "benchmark_cluster_state.py": "cluster_state",
                    "benchmark_persistence.py": "persistence",
                    "connect_performance_benchmark.py": "connect",
                }[script_name]
                output = Path(command[command.index("--json-output") + 1])
                package_root = Path(env["PYTHONPATH"].split(":")[0])
                extension = (
                    ".so"
                    if (package_root / "expra_connect/native.so").exists()
                    else ".py"
                )
                outcomes = tuple(
                    BenchmarkOutcome(
                        case_id,
                        case_id,
                        {"case": case_id},
                        index + 1,
                        64,
                        config,
                        measure(
                            lambda: None,
                            config,
                            clock_ns=iter((0, 1000)).__next__,
                        ),
                    )
                    for index, case_id in enumerate(suite_cases[suite])
                )
                write_results(
                    output,
                    RunMetadata(
                        1,
                        suite,
                        "3.4.5.6",
                        "3.12.3",
                        "Linux test",
                        {
                            "expra_connect.persistence": str(
                                package_root
                                / "expra_connect"
                                / f"persistence{extension}"
                            )
                        },
                        config,
                    ),
                    outcomes,
                )

            with patch(
                "scripts.benchmark_release_wheels.subprocess.run", fake_subprocess
            ):
                produced = run_release_supplement_benchmarks(
                    wheels, results, "python3", root
                )
                self.assertEqual(len(produced), 6)
                self.assertEqual(len(calls), 6)
                self.assertEqual(original.read_bytes(), before)
                saved = tuple(path.read_bytes() for path in produced)
                self.assertEqual(
                    run_release_supplement_benchmarks(wheels, results, "python3", root),
                    produced,
                )
                self.assertEqual(tuple(path.read_bytes() for path in produced), saved)

    def test_partial_supplements_fail_without_overwriting(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            wheels = root / "wheels"
            wheels.mkdir()
            for filename in (
                "expra_connect-3.4.5.6-cp312-cp312-linux_x86_64.whl",
                "expra_connect-3.4.5.6-py3-none-any.whl",
            ):
                (wheels / filename).write_bytes(b"wheel")
            results = root / "results"
            results.mkdir()
            existing = results / "3.4.5.6-native-connect-supplement.json"
            existing.write_bytes(b"keep")

            with self.assertRaisesRegex(ValueError, "partial supplement"):
                run_release_supplement_benchmarks(wheels, results, "python3", root)

            self.assertEqual(existing.read_bytes(), b"keep")

    def test_release_benchmark_cli_supports_direct_script_invocation(self) -> None:
        root = Path(__file__).resolve().parents[1]

        completed = subprocess.run(
            (
                sys.executable,
                str(root / "scripts" / "benchmark_release_wheels.py"),
                "--help",
            ),
            cwd=root,
            capture_output=True,
            check=False,
            text=True,
        )

        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertIn("--wheel-dir", completed.stdout)

    def test_benchmarks_only_artifact_wheels_with_controlled_subprocesses(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            wheels = root / "wheels"
            wheels.mkdir()
            wheel = wheels / "expra_connect-3.4.5.6-cp312-cp312-linux_x86_64.whl"
            with zipfile.ZipFile(wheel, "w") as archive:
                archive.writestr(
                    "expra_connect/__init__.py", "__version__ = '3.4.5.6'\n"
                )
                archive.writestr("expra_connect/persistence.py", "# package module\n")
            results = root / "results"
            results.mkdir()
            calls: list[tuple[str, ...]] = []
            config = MeasurementConfig(
                warmups=0, repetitions=1, track_allocations=False
            )

            def fake_subprocess(
                command: tuple[str, ...], *, cwd: Path, env: dict[str, str], check: bool
            ) -> None:
                calls.append(command)
                suite = (
                    "cluster_state"
                    if "benchmark_cluster_state.py" in command[1]
                    else "persistence"
                    if "benchmark_persistence.py" in command[1]
                    else "connect"
                )
                output = Path(command[command.index("--json-output") + 1])
                package_root = Path(env["PYTHONPATH"].split(":")[0])
                outcomes = (
                    tuple(
                        BenchmarkOutcome(
                            f"{size}:ProfileMigration:Profile migration",
                            "Profile migration",
                            {"shape": "flat_files", "files": size},
                            size,
                            size * 256,
                            config,
                            measure(
                                lambda: None,
                                config,
                                clock_ns=iter((0, 1000)).__next__,
                            ),
                        )
                        for size in (1, 16, 64)
                    )
                    if "--migration-file-counts" in command
                    else (
                        BenchmarkOutcome(
                            f"{suite}-case",
                            "case",
                            {"shape": "small"},
                            1,
                            1,
                            config,
                            measure(
                                lambda: None,
                                config,
                                clock_ns=iter((0, 1000)).__next__,
                            ),
                        ),
                    )
                )
                write_results(
                    output,
                    RunMetadata(
                        1,
                        suite,
                        "3.4.5.6",
                        "3.12.3",
                        "Linux test",
                        {
                            "expra_connect.persistence": str(
                                package_root / "expra_connect" / "persistence.py"
                            )
                        },
                        config,
                    ),
                    outcomes,
                )

            with patch(
                "scripts.benchmark_release_wheels.subprocess.run", fake_subprocess
            ):
                produced = run_release_benchmarks(wheels, results, "python3", root)
            self.assertEqual(len(produced), 3)
            self.assertTrue(all(path.is_file() for path in produced))
            persisted_case_ids = {
                outcome.case_id
                for outcome in load_results(
                    results / "3.4.5.6-native-persistence.json"
                ).outcomes
            }

        self.assertEqual(len(calls), 4)
        self.assertEqual(
            {Path(command[1]).name for command in calls},
            {
                "benchmark_cluster_state.py",
                "benchmark_persistence.py",
                "connect_performance_benchmark.py",
            },
        )
        self.assertIn("--migration-file-counts", calls[3])
        self.assertEqual(
            persisted_case_ids
            & {
                "1:ProfileMigration:Profile migration",
                "16:ProfileMigration:Profile migration",
                "64:ProfileMigration:Profile migration",
            },
            {
                "1:ProfileMigration:Profile migration",
                "16:ProfileMigration:Profile migration",
                "64:ProfileMigration:Profile migration",
            },
        )

    def test_complete_versioned_results_are_reused_without_overwriting(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            wheels = root / "wheels"
            wheels.mkdir()
            wheel = wheels / "expra_connect-3.4.5.6-cp312-cp312-linux_x86_64.whl"
            wheel.write_bytes(b"wheel placeholder")
            results = root / "results"
            results.mkdir()
            expected: list[Path] = []
            config = MeasurementConfig(
                warmups=0, repetitions=1, track_allocations=False
            )
            for suite in ("cluster_state", "persistence", "connect"):
                path = results / f"3.4.5.6-native-{suite}.json"
                expected.append(path)
                write_results(
                    path,
                    RunMetadata(
                        1,
                        suite,
                        "3.4.5.6",
                        "3.12.3",
                        "Linux test",
                        {
                            "expra_connect.persistence": "/old-wheel/expra_connect/persistence.py"
                        },
                        config,
                    ),
                    (
                        BenchmarkOutcome(
                            "case-1",
                            "case",
                            {"shape": "small"},
                            1,
                            1,
                            config,
                            measure(
                                lambda: None, config, clock_ns=iter((0, 1000)).__next__
                            ),
                        ),
                    ),
                )
            before = tuple(path.read_bytes() for path in expected)

            produced = run_release_benchmarks(wheels, results, "python3", root)

            self.assertEqual(produced, tuple(expected))
            self.assertEqual(tuple(path.read_bytes() for path in expected), before)

    def test_partial_versioned_results_fail_without_replacing_existing_data(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            wheels = root / "wheels"
            wheels.mkdir()
            (wheels / "expra_connect-3.4.5.6-py3-none-any.whl").write_bytes(b"wheel")
            results = root / "results"
            results.mkdir()
            existing = results / "3.4.5.6-universal-connect.json"
            existing.write_text("preserve", encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "partial benchmark results"):
                run_release_benchmarks(wheels, results, "python3", root)

            self.assertEqual(existing.read_text(encoding="utf-8"), "preserve")

    def test_legacy_2204_build_results_are_reused_without_rerunning_that_wheel(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            wheels = root / "wheels"
            wheels.mkdir()
            (wheels / "expra_connect-2.2.0.4-cp312-cp312-linux_x86_64.whl").write_bytes(
                b"existing wheel"
            )
            results = root / "results"
            results.mkdir()
            config = MeasurementConfig(
                warmups=0, repetitions=1, track_allocations=False
            )
            legacy_paths: list[Path] = []
            for suite in ("cluster_state", "persistence", "connect"):
                path = results / f"native-{suite}.json"
                legacy_paths.append(path)
                write_results(
                    path,
                    RunMetadata(
                        1,
                        suite,
                        "2.2.0.4",
                        "3.12.3",
                        "Linux test",
                        {"expra_connect.persistence": "/wheel/module.py"},
                        config,
                    ),
                    (
                        BenchmarkOutcome(
                            "case-1",
                            "case",
                            {"shape": "small"},
                            1,
                            1,
                            config,
                            measure(
                                lambda: None,
                                config,
                                clock_ns=iter((0, 1000)).__next__,
                            ),
                        ),
                    ),
                )

            with patch(
                "scripts.benchmark_release_wheels.subprocess.run",
                side_effect=AssertionError("historical wheel should not be rerun"),
            ):
                reused = run_release_benchmarks(wheels, results, "python3", root)

            self.assertEqual(reused, tuple(legacy_paths))
            self.assertFalse((results / "2.2.0.4-native-cluster_state.json").exists())


if __name__ == "__main__":
    unittest.main()

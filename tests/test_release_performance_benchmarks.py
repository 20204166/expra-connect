from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from scripts.benchmark_release_wheels import run_release_benchmarks
from scripts.benchmark_support import (
    BenchmarkOutcome,
    MeasurementConfig,
    RunMetadata,
    measure,
    write_results,
)


class ReleasePerformanceTests(unittest.TestCase):
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
                    (
                        BenchmarkOutcome(
                            f"{suite}-case",
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

            with patch(
                "scripts.benchmark_release_wheels.subprocess.run", fake_subprocess
            ):
                produced = run_release_benchmarks(wheels, results, "python3", root)
            self.assertEqual(len(produced), 3)
            self.assertTrue(all(path.is_file() for path in produced))

        self.assertEqual(len(calls), 3)
        self.assertEqual(
            {Path(command[1]).name for command in calls},
            {
                "benchmark_cluster_state.py",
                "benchmark_persistence.py",
                "connect_performance_benchmark.py",
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

"""Tests for the Expra Connect wheel versioning helper."""

from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from types import ModuleType


def _release_module() -> ModuleType:
    path = Path(__file__).parents[1] / "scripts" / "release.py"
    spec = importlib.util.spec_from_file_location("expra_connect_release", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("could not load release helper")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


release = _release_module()


def _make_package(root: Path, version: str) -> None:
    package = root / "src" / "expra_connect"
    package.mkdir(parents=True)
    (package / "_version.py").write_text(
        f'__version__ = "{version}"\n', encoding="utf-8"
    )
    (package / "__init__.py").write_text("# package\n", encoding="utf-8")
    (package / "runtime.py").write_text("# runtime\n", encoding="utf-8")


def _make_wheel(root: Path, version: str, tag: str = "py3-none-any") -> Path:
    wheel = root / "dist" / f"expra_connect-{version}-{tag}.whl"
    wheel.parent.mkdir(exist_ok=True)
    with zipfile.ZipFile(wheel, "w") as archive:
        for path in sorted((root / "src" / "expra_connect").glob("*.py")):
            archive.write(path, f"expra_connect/{path.name}")
    return wheel


def _stage_wheels(root: Path, version: str, tags: tuple[str, ...]) -> None:
    root.mkdir(parents=True, exist_ok=True)
    for tag in tags:
        (root / f"expra_connect-{version}-{tag}.whl").write_bytes(tag.encode())


class ReleaseTests(unittest.TestCase):
    def test_build_release_rejects_unknown_option_before_running_python(self) -> None:
        root = Path(__file__).parents[1]
        with tempfile.TemporaryDirectory() as directory:
            temporary = Path(directory)
            fake_python = temporary / "fake-python"
            marker = temporary / "python-was-run"
            fake_python.write_text(
                '#!/bin/sh\n: > "$INVOCATION_MARKER"\nexit 0\n',
                encoding="utf-8",
            )
            fake_python.chmod(0o755)
            environment = os.environ.copy()
            environment["PYTHON"] = str(fake_python)
            environment["INVOCATION_MARKER"] = str(marker)

            completed = subprocess.run(
                ["bash", str(root / "scripts" / "build_release.sh"), "--unknown"],
                capture_output=True,
                check=False,
                env=environment,
                text=True,
            )

            self.assertEqual(completed.returncode, 2, completed.stderr)
            self.assertFalse(marker.exists())

    def test_build_release_forwards_explicit_bump_to_prepare_build(self) -> None:
        root = Path(__file__).parents[1]
        with tempfile.TemporaryDirectory() as directory:
            temporary = Path(directory)
            fake_python = temporary / "fake-python"
            marker = temporary / "python-arguments"
            fake_python.write_text(
                '#!/bin/sh\nprintf "%s\\n" "$*" >> "$INVOCATION_MARKER"\n'
                'case "$*" in *"scripts/release.py prepare-build"*) exit 37;; esac\n',
                encoding="utf-8",
            )
            fake_python.chmod(0o755)
            environment = os.environ.copy()
            environment["PYTHON"] = str(fake_python)
            environment["INVOCATION_MARKER"] = str(marker)

            completed = subprocess.run(
                [
                    "bash",
                    str(root / "scripts" / "build_release.sh"),
                    "--universal",
                    "--bump",
                    "patch",
                ],
                capture_output=True,
                check=False,
                env=environment,
                text=True,
            )

            self.assertEqual(completed.returncode, 37, completed.stderr)
            self.assertIn(
                "scripts/release.py prepare-build --package-dir . --bump patch",
                marker.read_text(encoding="utf-8"),
            )

    def test_build_release_accepts_explicit_performance_benchmark_opt_out(self) -> None:
        root = Path(__file__).parents[1]
        with tempfile.TemporaryDirectory() as directory:
            temporary = Path(directory)
            fake_python = temporary / "fake-python"
            marker = temporary / "python-arguments"
            fake_python.write_text(
                '#!/bin/sh\nprintf "%s\\n" "$*" >> "$INVOCATION_MARKER"\n'
                'case "$*" in *"scripts/release.py prepare-build"*) exit 37;; esac\n',
                encoding="utf-8",
            )
            fake_python.chmod(0o755)
            environment = os.environ.copy()
            environment["PYTHON"] = str(fake_python)
            environment["INVOCATION_MARKER"] = str(marker)

            completed = subprocess.run(
                [
                    "bash",
                    str(root / "scripts" / "build_release.sh"),
                    "--skip-performance-benchmarks",
                ],
                capture_output=True,
                check=False,
                env=environment,
                text=True,
            )

            self.assertEqual(completed.returncode, 37, completed.stderr)
            self.assertIn("scripts/release.py prepare-build", marker.read_text())

    def test_performance_failure_stops_before_release_artifact_sync(self) -> None:
        root = Path(__file__).parents[1]
        with tempfile.TemporaryDirectory() as directory:
            temporary = Path(directory)
            fake_python = temporary / "fake-python"
            marker = temporary / "python-arguments"
            fake_python.write_text(
                '#!/bin/sh\nprintf "%s\\n" "$*" >> "$INVOCATION_MARKER"\n'
                'case "$*" in\n'
                '  *"-m pip wheel"*)\n'
                '    while [ "$#" -gt 0 ]; do\n'
                '      if [ "$1" = "--wheel-dir" ]; then\n'
                '        mkdir -p "$2"\n'
                '        touch "$2/expra_connect-9.9.9.9-cp312-cp312-linux_x86_64.whl"\n'
                "        break\n"
                "      fi\n"
                "      shift\n"
                "    done\n"
                "    exit 0;;\n"
                '  *"scripts/benchmark_release_wheels.py"*) exit 55;;\n'
                "esac\nexit 0\n",
                encoding="utf-8",
            )
            fake_python.chmod(0o755)
            environment = os.environ.copy()
            environment.update(
                {
                    "PYTHON": str(fake_python),
                    "INVOCATION_MARKER": str(marker),
                    "TMPDIR": str(temporary),
                }
            )

            completed = subprocess.run(
                ["bash", str(root / "scripts" / "build_release.sh"), "--bump", "none"],
                capture_output=True,
                check=False,
                env=environment,
                text=True,
            )

            self.assertEqual(completed.returncode, 55, completed.stderr)
            invocations = marker.read_text(encoding="utf-8")
            self.assertIn("scripts/benchmark_release_wheels.py", invocations)
            self.assertNotIn("sync-artifacts", invocations)

    def test_native_wheels_share_version_and_newest_native_is_manifest_baseline(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _make_package(root, "0.1.2.0")
            pure = _make_wheel(root, "0.1.2.0")
            native = _make_wheel(root, "0.1.2.0", "cp312-cp312-linux_x86_64")

            self.assertEqual(release._wheel_version(pure), "0.1.2.0")
            self.assertEqual(release._wheel_version(native), "0.1.2.0")
            self.assertEqual(release.newest_wheel(root), native)

    def test_sync_artifacts_adds_new_wheels_without_dropping_old_sums(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _make_package(root, "0.1.2.0")
            dist = root / "dist"
            dist.mkdir()
            old_wheel = dist / "expra_connect-0.1.1.0-py3-none-any.whl"
            old_wheel.write_bytes(b"unchanged historical wheel")
            old_row = "old-checksum  " + old_wheel.name
            (dist / "SHA256SUMS").write_text(old_row + "\n", encoding="utf-8")

            artifacts = root / "artifacts"
            artifacts.mkdir()
            tag = "cp312-cp312-linux_x86_64"
            wheel = artifacts / f"expra_connect-0.1.2.0-{tag}.whl"
            wheel.write_bytes(tag.encode())

            release.sync_artifacts(root, artifacts)

            sums = (dist / "SHA256SUMS").read_text(encoding="utf-8")
            self.assertIn(old_row, sums)
            name = f"expra_connect-0.1.2.0-{tag}.whl"
            self.assertEqual((dist / name).read_bytes(), tag.encode())
            self.assertIn(name, sums)
            self.assertEqual(old_wheel.read_bytes(), b"unchanged historical wheel")

    def test_sync_artifacts_rejects_pure_python_fallback(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _make_package(root, "0.1.2.0")
            artifacts = root / "artifacts"
            artifacts.mkdir()
            (artifacts / "expra_connect-0.1.2.0-py3-none-any.whl").write_bytes(
                b"pure wheel"
            )

            with self.assertRaisesRegex(FileNotFoundError, "native-only"):
                release.sync_artifacts(root, artifacts)

    def test_sync_artifacts_accepts_native_wheel_without_fallback(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _make_package(root, "0.1.2.0")
            artifacts = root / "artifacts"
            _stage_wheels(artifacts, "0.1.2.0", ("cp312-cp312-linux_x86_64",))

            release.sync_artifacts(root, artifacts)

            native_name = "expra_connect-0.1.2.0-cp312-cp312-linux_x86_64.whl"
            self.assertTrue((root / "dist" / native_name).is_file())
            self.assertNotIn(
                "py3-none-any.whl", (root / "dist" / "SHA256SUMS").read_text()
            )

    def test_sync_artifacts_accepts_opt_in_universal_with_native_sibling(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _make_package(root, "0.1.2.0")
            dist = root / "dist"
            dist.mkdir()
            old_name = "expra_connect-0.1.1.0-cp312-cp312-linux_x86_64.whl"
            old_row = "old-checksum  " + old_name
            (dist / old_name).write_bytes(b"old native wheel")
            (dist / "SHA256SUMS").write_text(old_row + "\n", encoding="utf-8")

            artifacts = root / "artifacts"
            _stage_wheels(
                artifacts,
                "0.1.2.0",
                ("cp312-cp312-linux_x86_64", "py3-none-any"),
            )

            release.sync_artifacts(root, artifacts, allow_universal=True)

            sums = (dist / "SHA256SUMS").read_text(encoding="utf-8")
            self.assertIn(old_row, sums)
            self.assertTrue(
                (dist / "expra_connect-0.1.2.0-cp312-cp312-linux_x86_64.whl").is_file()
            )
            self.assertTrue((dist / "expra_connect-0.1.2.0-py3-none-any.whl").is_file())
            self.assertIn("expra_connect-0.1.2.0-py3-none-any.whl", sums)

    def test_sync_artifacts_rejects_opt_in_universal_without_native_sibling(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _make_package(root, "0.1.2.0")
            artifacts = root / "artifacts"
            _stage_wheels(artifacts, "0.1.2.0", ("py3-none-any",))

            with self.assertRaisesRegex(FileNotFoundError, "native sibling"):
                release.sync_artifacts(root, artifacts, allow_universal=True)

            self.assertFalse((root / "dist" / "SHA256SUMS").exists())
            self.assertFalse(
                (root / "dist" / "expra_connect-0.1.2.0-py3-none-any.whl").exists()
            )

    def test_sync_artifacts_cli_requires_explicit_universal_opt_in(self) -> None:
        default_args = release._parser().parse_args(
            ["sync-artifacts", "--package-dir", ".", "--artifact-dir", "artifacts"]
        )
        opted_in_args = release._parser().parse_args(
            [
                "sync-artifacts",
                "--package-dir",
                ".",
                "--artifact-dir",
                "artifacts",
                "--allow-universal",
            ]
        )

        self.assertFalse(default_args.allow_universal)
        self.assertTrue(opted_in_args.allow_universal)

    def test_sync_artifacts_rejects_stale_checksum_before_copying(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _make_package(root, "0.1.2.0")
            dist = root / "dist"
            dist.mkdir()
            native_name = "expra_connect-0.1.2.0-cp312-cp312-linux_x86_64.whl"
            (dist / "SHA256SUMS").write_text(
                f"wrong-digest  {native_name}\n", encoding="utf-8"
            )
            artifacts = root / "artifacts"
            _stage_wheels(
                artifacts,
                "0.1.2.0",
                ("cp312-cp312-linux_x86_64",),
            )

            with self.assertRaisesRegex(ValueError, "checksum mismatch"):
                release.sync_artifacts(root, artifacts)

            self.assertFalse((dist / native_name).exists())

    def test_sync_artifacts_preflights_collisions_before_copying(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _make_package(root, "0.1.2.0")
            dist = root / "dist"
            dist.mkdir()
            native_name = "expra_connect-0.1.2.0-cp312-cp312-linux_x86_64.whl"
            (dist / native_name).write_bytes(b"different existing wheel")
            artifacts = root / "artifacts"
            _stage_wheels(
                artifacts,
                "0.1.2.0",
                ("cp312-cp312-linux_x86_64",),
            )

            with self.assertRaises(FileExistsError):
                release.sync_artifacts(root, artifacts)

            self.assertEqual(
                (dist / native_name).read_bytes(), b"different existing wheel"
            )

    def test_published_checksum_manifest_references_local_wheels(self) -> None:
        root = Path(__file__).parents[1]
        sums = root / "dist" / "SHA256SUMS"
        self.assertTrue(sums.is_file())
        entries = [
            line.split()
            for line in sums.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        self.assertTrue(entries)
        for _digest, filename in entries:
            self.assertTrue((root / "dist" / filename).is_file(), filename)

    def test_bump_levels_and_carry(self) -> None:
        self.assertEqual(release._next_version("0.1.2.0", "patch"), "0.1.2.1")
        self.assertEqual(release._next_version("0.1.2.9", "patch"), "0.1.3.0")
        self.assertEqual(release._next_version("0.1.2.0", "feature"), "0.1.3.0")
        self.assertEqual(release._next_version("0.1.2.0", "minor"), "0.2.0.0")

    def test_prepare_build_bumps_changed_source_and_stays_put(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _make_package(root, "0.1.2.0")
            first = release.prepare_build(root, bump_override="none")
            _make_wheel(root, first)
            self.assertEqual(release.prepare_build(root), first)
            (root / "src" / "expra_connect" / "runtime.py").write_text(
                "# changed runtime\n", encoding="utf-8"
            )
            self.assertEqual(release.prepare_build(root), "0.2.0.0")


if __name__ == "__main__":
    unittest.main()

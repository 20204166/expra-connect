"""Tests for the Expra Connect wheel versioning helper."""

from __future__ import annotations

import importlib.util
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

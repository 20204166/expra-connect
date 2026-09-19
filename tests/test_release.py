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


def _make_wheel(root: Path, version: str) -> Path:
    wheel = root / "dist" / f"expra_connect-{version}-py3-none-any.whl"
    wheel.parent.mkdir(exist_ok=True)
    with zipfile.ZipFile(wheel, "w") as archive:
        for path in sorted((root / "src" / "expra_connect").glob("*.py")):
            archive.write(path, f"expra_connect/{path.name}")
    return wheel


class ReleaseTests(unittest.TestCase):
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

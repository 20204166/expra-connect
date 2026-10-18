"""Tests for native and fallback wheel surface verification."""

from __future__ import annotations

import importlib.util
import tempfile
import unittest
import zipfile
from pathlib import Path
from types import ModuleType


def _verify_module() -> ModuleType:
    path = Path(__file__).parents[1] / "scripts" / "verify_wheel.py"
    spec = importlib.util.spec_from_file_location("expra_connect_verify_wheel", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("could not load wheel verifier")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


verify_wheel = _verify_module()


def _write_pure_wheel(path: Path, *, include_extension: bool = False) -> None:
    with zipfile.ZipFile(path, "w") as wheel:
        wheel.writestr("expra_connect/__init__.py", "")
        wheel.writestr("expra_connect/module.py", "")
        if include_extension:
            wheel.writestr("expra_connect/module.cpython-312-x86_64-linux-gnu.so", b"")
        wheel.writestr(
            "expra_connect-1.8.0.0.dist-info/METADATA",
            "Name: expra-connect\nVersion: 1.8.0.0\n"
            "Requires-Dist: cryptography\nRequires-Dist: typing-extensions\n"
            "Requires-Dist: zeroconf\n",
        )
        wheel.writestr(
            "expra_connect-1.8.0.0.dist-info/entry_points.txt",
            "[console_scripts]\nexpra-peer = expra_connect.cli:main\n",
        )
        wheel.writestr(
            "expra_connect-1.8.0.0.dist-info/WHEEL",
            "Wheel-Version: 1.0\nRoot-Is-Purelib: true\nTag: py3-none-any\n",
        )


def _write_native_wheel(
    path: Path,
    *,
    include_extensions: bool,
    omit_extension: str | None = None,
    tag: str = "cp312-cp312-linux_x86_64",
    extension_abi: str = "312",
) -> None:
    with zipfile.ZipFile(path, "w") as wheel:
        wheel.writestr("expra_connect/__init__.py", "")
        wheel.writestr("expra_connect/persistence.py", "")
        wheel.writestr("expra_connect/remote/__init__.py", "")
        wheel.writestr("expra_connect/remote/session.py", "")
        wheel.writestr("expra_connect/remote/wire_protocol.py", "")
        wheel.writestr("expra_connect/cluster/__init__.py", "")
        for module in ("models", "operations", "roles", "state"):
            wheel.writestr(f"expra_connect/cluster/{module}.py", "")
        if include_extensions:
            modules = (
                "expra_connect/cluster/models",
                "expra_connect/cluster/operations",
                "expra_connect/cluster/roles",
                "expra_connect/cluster/state",
                "expra_connect/remote/session",
                "expra_connect/remote/wire_protocol",
                "expra_connect/persistence",
            )
            for module in modules:
                if module == omit_extension:
                    continue
                wheel.writestr(
                    f"{module}.cpython-{extension_abi}-x86_64-linux-gnu.so",
                    b"",
                )
        wheel.writestr(
            "expra_connect-1.8.0.0.dist-info/METADATA",
            "Name: expra-connect\nVersion: 1.8.0.0\n"
            "Requires-Dist: cryptography\nRequires-Dist: typing-extensions\n"
            "Requires-Dist: zeroconf\n",
        )
        wheel.writestr(
            "expra_connect-1.8.0.0.dist-info/entry_points.txt",
            "[console_scripts]\nexpra-peer = expra_connect.cli:main\n",
        )
        wheel.writestr(
            "expra_connect-1.8.0.0.dist-info/WHEEL",
            f"Wheel-Version: 1.0\nRoot-Is-Purelib: false\nTag: {tag}\n",
        )


class VerifyWheelTests(unittest.TestCase):
    def test_pure_wheel_requires_matching_source_and_has_no_extensions(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            wheel_path = root / "expra_connect-1.8.0.0-py3-none-any.whl"
            source_package = root / "source" / "expra_connect"
            source_package.mkdir(parents=True)
            (source_package / "__init__.py").write_text("", encoding="utf-8")
            (source_package / "module.py").write_text("", encoding="utf-8")

            _write_pure_wheel(wheel_path)
            verify_wheel.verify(
                wheel_path,
                expected_version="1.8.0.0",
                source_package=source_package,
            )

            _write_pure_wheel(wheel_path, include_extension=True)
            with self.assertRaisesRegex(
                SystemExit, "pure-Python wheel contains native"
            ):
                verify_wheel.verify(
                    wheel_path,
                    expected_version="1.8.0.0",
                    source_package=source_package,
                )

    def test_native_wheel_requires_extensions_and_python_fallback_sources(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            wheel_path = Path(directory) / (
                "expra_connect-1.8.0.0-cp312-cp312-linux_x86_64.whl"
            )
            _write_native_wheel(wheel_path, include_extensions=True)
            verify_wheel.verify(wheel_path, expected_version="1.8.0.0")

            _write_native_wheel(wheel_path, include_extensions=False)
            with self.assertRaises(SystemExit):
                verify_wheel.verify(wheel_path, expected_version="1.8.0.0")

    def test_native_wheel_requires_cluster_roles_extension(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            wheel_path = Path(directory) / (
                "expra_connect-1.8.0.0-cp312-cp312-linux_x86_64.whl"
            )
            _write_native_wheel(
                wheel_path,
                include_extensions=True,
                omit_extension="expra_connect/cluster/roles",
            )

            with self.assertRaisesRegex(SystemExit, "expra_connect/cluster/roles"):
                verify_wheel.verify(wheel_path, expected_version="1.8.0.0")

    def test_native_wheel_requires_persistence_extension(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            wheel_path = Path(directory) / (
                "expra_connect-1.8.0.0-cp312-cp312-linux_x86_64.whl"
            )
            _write_native_wheel(
                wheel_path,
                include_extensions=True,
                omit_extension="expra_connect/persistence",
            )

            with self.assertRaisesRegex(SystemExit, "expra_connect/persistence"):
                verify_wheel.verify(wheel_path, expected_version="1.8.0.0")

    def test_native_wheel_requires_cluster_state_extensions(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            wheel_path = Path(directory) / (
                "expra_connect-1.8.0.0-cp312-cp312-linux_x86_64.whl"
            )
            _write_native_wheel(
                wheel_path,
                include_extensions=True,
                omit_extension="expra_connect/cluster/state",
            )

            with self.assertRaisesRegex(SystemExit, "expra_connect/cluster/state"):
                verify_wheel.verify(wheel_path, expected_version="1.8.0.0")

    def test_source_package_check_rejects_incomplete_wheel(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            wheel_path = root / ("expra_connect-1.8.0.0-cp312-cp312-linux_x86_64.whl")
            _write_native_wheel(wheel_path, include_extensions=True)
            source_package = root / "source" / "expra_connect"
            (source_package / "remote").mkdir(parents=True)
            for relative in (
                "__init__.py",
                "remote/__init__.py",
                "remote/session.py",
                "remote/wire_protocol.py",
                "extra.py",
            ):
                (source_package / relative).write_text("", encoding="utf-8")

            with self.assertRaisesRegex(SystemExit, "missing Python source files"):
                verify_wheel.verify(
                    wheel_path,
                    expected_version="1.8.0.0",
                    source_package=source_package,
                )

    def test_native_wheel_rejects_non_cpython_platform_tag(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            wheel_path = Path(directory) / (
                "expra_connect-1.8.0.0-py2-none-linux_x86_64.whl"
            )
            _write_native_wheel(
                wheel_path,
                include_extensions=True,
                tag="py2-none-linux_x86_64",
            )

            with self.assertRaisesRegex(SystemExit, "CPython platform tag"):
                verify_wheel.verify(wheel_path)

    def test_native_wheel_rejects_interpreter_abi_version_mismatch(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            tag = "cp312-cp311-linux_x86_64"
            wheel_path = Path(directory) / f"expra_connect-1.8.0.0-{tag}.whl"
            _write_native_wheel(
                wheel_path,
                include_extensions=True,
                tag=tag,
            )

            with self.assertRaisesRegex(SystemExit, "interpreter and ABI tags"):
                verify_wheel.verify(wheel_path)

    def test_native_wheel_rejects_extension_abi_mismatch(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            wheel_path = Path(directory) / (
                "expra_connect-1.8.0.0-cp313-cp313-linux_x86_64.whl"
            )
            _write_native_wheel(
                wheel_path,
                include_extensions=True,
                tag="cp313-cp313-linux_x86_64",
                extension_abi="312",
            )

            with self.assertRaisesRegex(SystemExit, "extension ABI does not match"):
                verify_wheel.verify(wheel_path)

    def test_wheel_rejects_archive_files_outside_package_and_dist_info(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            wheel_path = Path(directory) / (
                "expra_connect-1.8.0.0-cp312-cp312-linux_x86_64.whl"
            )
            _write_native_wheel(wheel_path, include_extensions=True)
            with zipfile.ZipFile(wheel_path, "a") as wheel:
                wheel.writestr("outside/payload.txt", "unexpected")

            with self.assertRaisesRegex(SystemExit, "unexpected wheel archive paths"):
                verify_wheel.verify(wheel_path)

    def test_wheel_rejects_parent_traversal_inside_package_prefix(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            wheel_path = Path(directory) / (
                "expra_connect-1.8.0.0-cp312-cp312-linux_x86_64.whl"
            )
            _write_native_wheel(wheel_path, include_extensions=True)
            with zipfile.ZipFile(wheel_path, "a") as wheel:
                wheel.writestr("expra_connect/../../outside.txt", "unexpected")

            with self.assertRaisesRegex(SystemExit, "unsafe wheel archive path"):
                verify_wheel.verify(wheel_path)


if __name__ == "__main__":
    unittest.main()

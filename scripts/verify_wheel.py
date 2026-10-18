#!/usr/bin/env python3
"""Verify that a wheel contains only the intended headless package surface."""

from __future__ import annotations

import argparse
import re
import zipfile
from email.parser import Parser
from pathlib import Path, PurePosixPath

FORBIDDEN = (
    "tkinter",
    "maintenance",
    "expra_engine",
    "system_analyzer",
    "tests/",
    ".key",
    ".pem",
    ".crt",
    "secret",
)
REQUIRED_DEPENDENCIES = ("cryptography", "typing-extensions", "zeroconf")
WHEEL_NAME = re.compile(
    r"^expra_connect-(\d+\.\d+\.\d+\.\d+)-([^-.]+-[^-.]+-[^-.]+)\.whl$"
)
CPYTHON_NATIVE_TAG = re.compile(
    r"^cp(?P<interpreter>\d+)-cp(?P<abi>\d+)-"
    r"(?:linux_[a-z0-9_]+|manylinux[a-z0-9_]+|win_(?:amd64|arm64)|win32)$"
)
EXTENSION_SUFFIXES = (".so", ".pyd")
CYTHON_MODULES = (
    "expra_connect/cluster/models",
    "expra_connect/cluster/operations",
    "expra_connect/cluster/roles",
    "expra_connect/cluster/state",
    "expra_connect/remote/session",
    "expra_connect/remote/wire_protocol",
    "expra_connect/persistence",
)


def verify(
    path: Path,
    expected_version: str | None = None,
    source_package: Path | None = None,
) -> None:
    filename_match = WHEEL_NAME.match(path.name)
    if filename_match is None:
        raise SystemExit(f"unexpected wheel filename: {path.name}")
    filename_version, filename_tag = filename_match.groups()
    if expected_version is not None and filename_version != expected_version:
        raise SystemExit(f"wheel filename has unexpected version: {filename_version!r}")
    with zipfile.ZipFile(path) as wheel:
        names = wheel.namelist()
        unsafe_paths = [
            name
            for name in names
            if "\\" in name
            or "\x00" in name
            or PurePosixPath(name).is_absolute()
            or ".." in PurePosixPath(name).parts
        ]
        if unsafe_paths:
            raise SystemExit("unsafe wheel archive path: " + ", ".join(unsafe_paths))
        lowered = "\n".join(names).lower()
        forbidden = [item for item in FORBIDDEN if item in lowered]
        if forbidden:
            raise SystemExit(f"forbidden wheel paths: {', '.join(forbidden)}")
        dist_info_root = f"expra_connect-{filename_version}.dist-info/"
        outside_paths = [
            name
            for name in names
            if not name.startswith("expra_connect/")
            and not name.startswith(dist_info_root)
        ]
        if outside_paths:
            raise SystemExit(
                "unexpected wheel archive paths: " + ", ".join(outside_paths)
            )
        package_files = [
            name
            for name in names
            if name.startswith("expra_connect/") and name.endswith(".py")
        ]
        if "expra_connect/__init__.py" not in package_files:
            raise SystemExit("wheel does not contain expra_connect/__init__.py")
        if source_package is not None:
            expected_sources = {
                f"expra_connect/{source.relative_to(source_package).as_posix()}"
                for source in source_package.rglob("*.py")
                if "__pycache__" not in source.parts
            }
            actual_sources = set(package_files)
            missing_sources = sorted(expected_sources - actual_sources)
            if missing_sources:
                raise SystemExit(
                    "wheel is missing Python source files: "
                    + ", ".join(missing_sources)
                )
            extra_sources = sorted(actual_sources - expected_sources)
            if extra_sources:
                raise SystemExit(
                    "wheel contains unexpected Python source files: "
                    + ", ".join(extra_sources)
                )
        metadata_name = next(
            (name for name in names if name.endswith(".dist-info/METADATA")), None
        )
        if metadata_name is None:
            raise SystemExit("wheel does not contain dist-info metadata")
        metadata = Parser().parsestr(wheel.read(metadata_name).decode("utf-8"))
        if metadata.get("Name") != "expra-connect":
            raise SystemExit("wheel metadata has the wrong package name")
        version = metadata.get("Version")
        if not version or version != filename_version:
            raise SystemExit(f"wheel metadata has unexpected version: {version!r}")
        dependencies = "\n".join(metadata.get_all("Requires-Dist", []))
        missing = [item for item in REQUIRED_DEPENDENCIES if item not in dependencies]
        if missing:
            raise SystemExit(f"wheel metadata is missing dependencies: {missing}")
        entry_points = next(
            (name for name in names if name.endswith(".dist-info/entry_points.txt")),
            None,
        )
        if (
            entry_points is None
            or "expra-peer = expra_connect.cli:main"
            not in wheel.read(entry_points).decode("utf-8")
        ):
            raise SystemExit("wheel is missing the expra-peer console entry point")

        wheel_metadata = next(
            (name for name in names if name.endswith(".dist-info/WHEEL")), None
        )
        if wheel_metadata is None:
            raise SystemExit("wheel does not contain WHEEL metadata")
        wheel_text = wheel.read(wheel_metadata).decode("utf-8")
        if f"Tag: {filename_tag}" not in wheel_text.splitlines():
            raise SystemExit("wheel tag metadata does not match its filename")

        extensions = [name for name in names if name.endswith(EXTENSION_SUFFIXES)]
        if filename_tag == "py3-none-any":
            if extensions:
                raise SystemExit("pure-Python wheel contains native extensions")
        else:
            native_tag = CPYTHON_NATIVE_TAG.fullmatch(filename_tag)
            if native_tag is None:
                raise SystemExit("native wheel must use a CPython platform tag")
            if native_tag.group("interpreter") != native_tag.group("abi"):
                raise SystemExit(
                    "native wheel has inconsistent interpreter and ABI tags"
                )
            if not extensions:
                raise SystemExit("native wheel contains no extension modules")
            extension_marker = (
                f".cp{native_tag.group('interpreter')}-"
                if filename_tag.endswith(("-win_amd64", "-win_arm64", "-win32"))
                else f".cpython-{native_tag.group('interpreter')}-"
            )
            missing_modules: list[str] = []
            for module in CYTHON_MODULES:
                module_extensions = [
                    name for name in extensions if name.startswith(f"{module}.")
                ]
                if not module_extensions or f"{module}.py" not in names:
                    missing_modules.append(module)
                elif not any(extension_marker in name for name in module_extensions):
                    raise SystemExit(f"{module} extension ABI does not match wheel tag")
            if missing_modules:
                raise SystemExit(
                    "native wheel is missing compiled modules or Python sources: "
                    + ", ".join(missing_modules)
                )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("wheel", type=Path)
    parser.add_argument("--expected-version")
    parser.add_argument("--source-package", type=Path)
    args = parser.parse_args()
    verify(args.wheel, args.expected_version, args.source_package)
    print(f"verified {args.wheel}")

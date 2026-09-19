#!/usr/bin/env python3
"""Verify that a wheel contains only the intended headless package surface."""

from __future__ import annotations

import sys
import zipfile
from email.parser import Parser
from pathlib import Path

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


def verify(path: Path, expected_version: str | None = None) -> None:
    with zipfile.ZipFile(path) as wheel:
        names = wheel.namelist()
        lowered = "\n".join(names).lower()
        forbidden = [item for item in FORBIDDEN if item in lowered]
        if forbidden:
            raise SystemExit(f"forbidden wheel paths: {', '.join(forbidden)}")
        package_files = [
            name
            for name in names
            if name.startswith("expra_connect/") and name.endswith(".py")
        ]
        if "expra_connect/__init__.py" not in package_files:
            raise SystemExit("wheel does not contain expra_connect/__init__.py")
        metadata_name = next(
            (name for name in names if name.endswith(".dist-info/METADATA")), None
        )
        if metadata_name is None:
            raise SystemExit("wheel does not contain dist-info metadata")
        metadata = Parser().parsestr(wheel.read(metadata_name).decode("utf-8"))
        if metadata.get("Name") != "expra-connect":
            raise SystemExit("wheel metadata has the wrong package name")
        version = metadata.get("Version")
        if not version or (
            expected_version is not None and version != expected_version
        ):
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


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("usage: verify_wheel.py WHEEL")
    verify(Path(sys.argv[1]))
    print(f"verified {sys.argv[1]}")

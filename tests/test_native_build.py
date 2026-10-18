from __future__ import annotations

import os
import runpy
import sys
import unittest
from collections.abc import Callable
from pathlib import Path
from types import ModuleType
from typing import Any
from unittest.mock import patch


class _CythonBuildModule(ModuleType):
    cythonize: Callable[..., Any]


class _CythonPackage(ModuleType):
    Build: _CythonBuildModule


class NativeBuildTests(unittest.TestCase):
    def test_native_build_selects_expected_modules(self) -> None:
        root = Path(__file__).parents[1]
        cython_package = _CythonPackage("Cython")
        cython_build = _CythonBuildModule("Cython.Build")

        def cythonize(extensions: list[Any], **_: Any) -> list[Any]:
            return extensions

        cython_build.cythonize = cythonize
        cython_package.Build = cython_build
        with (
            patch.dict(os.environ, {"EXPRA_CONNECT_CYTHON": "1"}),
            patch.dict(
                sys.modules,
                {"Cython": cython_package, "Cython.Build": cython_build},
            ),
            patch("setuptools.setup") as setup_call,
        ):
            runpy.run_path(str(root / "setup.py"), run_name="native_build_test")

        extensions = setup_call.call_args.kwargs["ext_modules"]
        self.assertEqual(
            {extension.name for extension in extensions},
            {
                "expra_connect.cluster.models",
                "expra_connect.cluster.operations",
                "expra_connect.cluster.state",
                "expra_connect.cluster.roles",
                "expra_connect.remote.session",
                "expra_connect.remote.wire_protocol",
            },
        )


if __name__ == "__main__":
    unittest.main()

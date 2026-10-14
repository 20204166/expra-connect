"""Optional, selective Cython extensions for CPython native wheels."""

from __future__ import annotations

import os
import sys

from setuptools import Extension, setup


def _native_extensions() -> list[Extension]:
    if os.environ.get("EXPRA_CONNECT_CYTHON") != "1":
        return []
    if sys.implementation.name != "cpython":
        raise RuntimeError("Cython native builds are supported on CPython only")

    from Cython.Build import cythonize

    modules = (
        "expra_connect.remote.session",
        "expra_connect.remote.wire_protocol",
    )
    extensions = [
        Extension(module, [f"src/{module.replace('.', '/')}.py"]) for module in modules
    ]
    return cythonize(
        extensions,
        compiler_directives={"language_level": 3, "annotation_typing": True},
        build_dir="build/cython",
    )


setup(ext_modules=_native_extensions())

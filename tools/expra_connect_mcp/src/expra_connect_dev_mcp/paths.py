"""Read-only path containment for configured source roots."""

from __future__ import annotations

import os
from pathlib import Path


class PathAccessError(ValueError):
    """Raised when a requested path leaves its configured root."""


def resolve_source_path(root: Path, requested: str) -> Path:
    if "\x00" in requested:
        raise PathAccessError("path contains a NUL byte")
    relative = Path(requested)
    if relative.is_absolute() or any(part == ".." for part in relative.parts):
        raise PathAccessError("absolute and traversal paths are not allowed")
    root_resolved = root.expanduser().resolve(strict=True)
    candidate = (root_resolved / relative).resolve(strict=True)
    try:
        candidate.relative_to(root_resolved)
    except ValueError as error:
        raise PathAccessError("path escapes its configured root") from error
    if not candidate.is_file():
        raise PathAccessError("source path is not a regular file")
    if os.path.islink(root / relative):
        try:
            (root / relative).resolve(strict=True).relative_to(root_resolved)
        except ValueError as error:
            raise PathAccessError("symlink escapes its configured root") from error
    return candidate


def resolve_source_directory(root: Path, requested: str) -> Path:
    if "\x00" in requested:
        raise PathAccessError("path contains a NUL byte")
    relative = Path(requested)
    if relative.is_absolute() or any(part == ".." for part in relative.parts):
        raise PathAccessError("absolute and traversal paths are not allowed")
    root_resolved = root.expanduser().resolve(strict=True)
    candidate = (root_resolved / relative).resolve(strict=True)
    try:
        candidate.relative_to(root_resolved)
    except ValueError as error:
        raise PathAccessError("path escapes its configured root") from error
    if not candidate.is_dir():
        raise PathAccessError("search path is not a directory")
    return candidate

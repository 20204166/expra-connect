"""TOML configuration and configured-interpreter resolution."""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

if sys.version_info >= (3, 11):
    import tomllib
else:
    import tomli as tomllib  # type: ignore[import-not-found]


@dataclass(frozen=True, slots=True)
class WorkspaceConfig:
    connect_root: Path
    python: str = "auto"


@dataclass(frozen=True, slots=True)
class ReferenceConfig:
    path: Path
    read_only: bool = True


@dataclass(frozen=True, slots=True)
class ExecutionConfig:
    allow_network: bool = False
    allow_lan_discovery: bool = False
    allow_pairing: bool = False
    allow_trust_mutation: bool = False
    allow_cluster_mutation: bool = False
    command_timeout_seconds: float = 600.0
    max_output_kb: int = 256
    max_parallel_commands: int = 2


@dataclass(frozen=True, slots=True)
class McpConfig:
    workspace: WorkspaceConfig
    references: dict[str, ReferenceConfig]
    execution: ExecutionConfig = ExecutionConfig()
    config_path: Path | None = None


@dataclass(frozen=True, slots=True)
class ResolvedPython:
    path: Path
    used_fallback: bool
    fallback_reason: str | None = None


def _path(value: Any, base: Path) -> Path:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("configured path must be a non-empty string")
    candidate = Path(value).expanduser()
    return (candidate if candidate.is_absolute() else base / candidate).resolve()


def default_config(root: Path | None = None) -> McpConfig:
    connect_root = (root or Path.cwd()).resolve()
    reference = connect_root.parent / "exp"
    if not reference.is_dir():
        reference = connect_root
    return McpConfig(
        WorkspaceConfig(connect_root),
        {"exp_ui": ReferenceConfig(reference)},
    )


def load_config(path: Path | None = None) -> McpConfig:
    if path is None or not path.exists():
        return default_config()
    config_path = path.expanduser().resolve()
    with config_path.open("rb") as file:
        raw = tomllib.load(file)
    if raw.get("schema_version", 1) != 1:
        raise ValueError("unsupported MCP configuration schema")
    base = config_path.parent
    workspace = raw.get("workspace", {})
    references = raw.get("references", {})
    execution = raw.get("execution", {})
    if (
        not isinstance(workspace, dict)
        or not isinstance(references, dict)
        or not isinstance(execution, dict)
    ):
        raise TypeError("workspace and references must be TOML tables")
    configured_python = workspace.get("python", "auto")
    if not isinstance(configured_python, str):
        raise TypeError("workspace.python must be a string")
    root = _path(workspace.get("connect_root", "."), base)
    parsed_references: dict[str, ReferenceConfig] = {}
    for name, value in references.items():
        if not isinstance(name, str) or not isinstance(value, dict):
            raise TypeError("reference entries are malformed")
        read_only = value.get("read_only", True)
        if not isinstance(read_only, bool):
            raise TypeError("reference read_only must be a boolean")
        parsed_references[name] = ReferenceConfig(
            _path(value.get("path"), base), read_only
        )
    if "exp_ui" not in parsed_references:
        parsed_references["exp_ui"] = ReferenceConfig(root.parent / "exp")
    return McpConfig(
        WorkspaceConfig(root, configured_python),
        parsed_references,
        ExecutionConfig(
            **{
                field: execution[field]
                for field in ExecutionConfig.__dataclass_fields__
                if field in execution
            }
        ),
        config_path,
    )


def resolve_python(
    root: Path, configured: str = "auto", *, current: Path | None = None
) -> ResolvedPython:
    fallback = (current or Path(sys.executable)).absolute()
    if configured != "auto":
        selected = Path(configured).expanduser()
        if not selected.is_absolute():
            selected = root / selected
        selected = selected.absolute()
        if not selected.is_file():
            raise ValueError(f"configured Python does not exist: {selected}")
        return ResolvedPython(selected, False)
    candidates = (
        root / ".venv" / "bin" / "python",
        root / ".venv" / "Scripts" / "python.exe",
    )
    for candidate in candidates:
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return ResolvedPython(candidate.absolute(), False)
    return ResolvedPython(
        fallback,
        True,
        "workspace virtual environment was not found; using current interpreter",
    )

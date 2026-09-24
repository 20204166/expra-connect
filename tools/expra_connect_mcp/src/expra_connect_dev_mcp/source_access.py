"""Read-only source and repository inspection."""

from __future__ import annotations

import os
import shutil
from pathlib import Path

from .command_runner import run_fixed_command
from .config import McpConfig
from .models import (
    RepoInspectResult,
    SourceMatch,
    SourceReadResult,
    SourceSearchResult,
)
from .paths import PathAccessError, resolve_source_directory, resolve_source_path


def source_root(config: McpConfig, source_id: str) -> Path:
    if source_id == "connect":
        return config.workspace.connect_root
    reference = config.references.get(source_id)
    if reference is None:
        raise PathAccessError(f"unknown source id: {source_id}")
    if not reference.read_only:
        raise PathAccessError(f"source is not marked read-only: {source_id}")
    return reference.path


def read_source(
    config: McpConfig, source_id: str, requested: str, *, max_output_kb: int
) -> SourceReadResult:
    path = resolve_source_path(source_root(config, source_id), requested)
    limit = max(1, max_output_kb) * 1024
    with path.open("rb") as file:
        content = file.read(limit + 1)
    truncated = len(content) > limit
    content = content[:limit]
    return SourceReadResult(
        source_id=source_id,
        path=requested,
        content=content.decode("utf-8", errors="replace"),
        bytes_read=len(content),
        truncated=truncated,
    )


async def search_source(
    config: McpConfig,
    source_id: str,
    query: str,
    requested: str = ".",
    *,
    max_output_kb: int,
) -> SourceSearchResult:
    if not query or "\x00" in query or len(query) > 256:
        raise ValueError("query must be 1-256 characters and contain no NUL")
    root = source_root(config, source_id)
    search_root = resolve_source_directory(root, requested)
    if shutil.which("rg") is None:
        return _python_search(root, search_root, source_id, query, max_output_kb)
    result = await run_fixed_command(
        [
            "rg",
            "--line-number",
            "--no-heading",
            "--color",
            "never",
            query,
            str(search_root),
        ],
        cwd=root,
        timeout_seconds=config.execution.command_timeout_seconds,
        max_output_kb=max_output_kb,
    )
    if result.exit_code is None and result.stderr:
        raise RuntimeError(result.stderr.strip() or "source search timed out")
    matches: list[SourceMatch] = []
    for line in result.stdout.splitlines():
        try:
            filename, line_number, text = line.split(":", 2)
            relative = str(Path(filename).resolve().relative_to(root.resolve()))
            matches.append(SourceMatch(path=relative, line=int(line_number), text=text))
        except (ValueError, OSError):
            continue
    return SourceSearchResult(
        source_id=source_id,
        query=query,
        matches=matches,
        truncated=result.output_truncated,
    )


def _python_search(
    root: Path, search_root: Path, source_id: str, query: str, max_output_kb: int
) -> SourceSearchResult:
    limit = max(1, max_output_kb) * 1024
    consumed = 0
    matches: list[SourceMatch] = []
    truncated = False
    for directory, names, files in os.walk(search_root, followlinks=False):
        names[:] = [name for name in names if name not in {".git", ".venv", "build"}]
        for name in files:
            path = Path(directory) / name
            try:
                lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
            except OSError:
                continue
            for number, text in enumerate(lines, 1):
                if query not in text:
                    continue
                match = SourceMatch(
                    path=str(path.relative_to(root)), line=number, text=text
                )
                consumed += len(text.encode("utf-8"))
                if consumed > limit:
                    truncated = True
                    return SourceSearchResult(
                        source_id=source_id,
                        query=query,
                        matches=matches,
                        truncated=truncated,
                    )
                matches.append(match)
    return SourceSearchResult(
        source_id=source_id,
        query=query,
        matches=matches,
        truncated=truncated,
    )


async def inspect_repo(config: McpConfig) -> RepoInspectResult:
    root = config.workspace.connect_root
    python_files = 0
    documentation_files = 0
    entries: list[str] = []
    for entry in sorted(root.iterdir(), key=lambda item: item.name):
        entries.append(entry.name)
    for directory, names, files in os.walk(root, followlinks=False):
        names[:] = [name for name in names if name not in {".git", ".venv", "build"}]
        for name in files:
            if name.endswith(".py"):
                python_files += 1
            if name.endswith((".md", ".rst", ".txt")):
                documentation_files += 1
    revision, dirty = await _git_state(config)
    return RepoInspectResult(
        repository_root=str(root),
        top_level_entries=entries,
        python_files=python_files,
        documentation_files=documentation_files,
        git_revision=revision,
        git_dirty=dirty,
    )


async def _git_state(config: McpConfig) -> tuple[str | None, bool | None]:
    revision = await run_fixed_command(
        ["git", "rev-parse", "HEAD"],
        cwd=config.workspace.connect_root,
        timeout_seconds=config.execution.command_timeout_seconds,
        max_output_kb=config.execution.max_output_kb,
    )
    status = await run_fixed_command(
        ["git", "status", "--porcelain"],
        cwd=config.workspace.connect_root,
        timeout_seconds=config.execution.command_timeout_seconds,
        max_output_kb=config.execution.max_output_kb,
    )
    return (
        revision.stdout.strip() if revision.exit_code == 0 else None,
        bool(status.stdout.strip()) if status.exit_code == 0 else None,
    )

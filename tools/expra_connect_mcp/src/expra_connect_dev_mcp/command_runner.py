"""Bounded subprocess execution with no shell or free-form command API."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class CommandResult:
    command: list[str]
    exit_code: int | None
    duration_seconds: float
    stdout: str
    stderr: str
    timed_out: bool
    output_truncated: bool


async def run_fixed_command(
    command: Sequence[str],
    *,
    cwd: Path,
    timeout_seconds: float,
    max_output_kb: int,
) -> CommandResult:
    started = time.monotonic()
    limit = max(1, max_output_kb) * 1024
    try:
        process = await asyncio.create_subprocess_exec(
            *command,
            cwd=str(cwd),
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except FileNotFoundError:
        return CommandResult(
            list(command),
            127,
            time.monotonic() - started,
            "",
            f"executable not found: {command[0]}",
            False,
            False,
        )
    try:
        stdout_bytes, stderr_bytes = await asyncio.wait_for(
            process.communicate(), timeout=timeout_seconds
        )
        timed_out = False
    except asyncio.TimeoutError:
        process.kill()
        stdout_bytes, stderr_bytes = await process.communicate()
        timed_out = True
    output_truncated = len(stdout_bytes) > limit or len(stderr_bytes) > limit
    return CommandResult(
        list(command),
        None if timed_out else process.returncode,
        time.monotonic() - started,
        stdout_bytes[:limit].decode("utf-8", errors="replace"),
        stderr_bytes[:limit].decode("utf-8", errors="replace"),
        timed_out,
        output_truncated,
    )

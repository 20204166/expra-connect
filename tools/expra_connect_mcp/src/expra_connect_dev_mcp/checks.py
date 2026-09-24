"""Allowlisted repository check profiles."""

from __future__ import annotations

from .command_runner import run_fixed_command
from .config import McpConfig, resolve_python
from .models import CheckResult


async def run_check(config: McpConfig, profile: str) -> CheckResult:
    python = resolve_python(config.workspace.connect_root, config.workspace.python).path
    commands: dict[str, list[str]] = {
        "focused": [str(python), "-m", "unittest", "discover", "-s", "tests"],
        "full": [str(python), "-m", "unittest", "discover", "-s", "tests"],
        "lint": ["ruff", "check", "src", "tests"],
        "types": ["pyright", "src", "tests"],
    }
    command = commands.get(profile)
    if command is None:
        raise ValueError(
            "unknown check profile; choose one of: " + ", ".join(sorted(commands))
        )
    result = await run_fixed_command(
        command,
        cwd=config.workspace.connect_root,
        timeout_seconds=config.execution.command_timeout_seconds,
        max_output_kb=config.execution.max_output_kb,
    )
    return CheckResult(
        profile=profile,
        command=result.command,
        exit_code=result.exit_code,
        duration_seconds=result.duration_seconds,
        stdout=result.stdout,
        stderr=result.stderr,
        timed_out=result.timed_out,
        output_truncated=result.output_truncated,
    )

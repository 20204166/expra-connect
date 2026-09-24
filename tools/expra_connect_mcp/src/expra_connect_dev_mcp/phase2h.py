"""Measured latency, retry, diagnostics, and retention evidence."""

from __future__ import annotations

import json
from typing import Any, Literal, cast

from mcp.server.mcpserver.exceptions import ToolError

from .command_runner import run_fixed_command
from .config import McpConfig, resolve_python
from .models import PerformanceAuditResult, RetentionEvidence, RetryEvidence

PerformanceAction = Literal["all", "latency", "retry", "retention"]
_ACTIONS = {"all", "latency", "retry", "retention"}


async def performance_audit(
    config: McpConfig, action: str = "all"
) -> PerformanceAuditResult:
    if action not in _ACTIONS:
        raise ToolError("unsupported performance audit action")
    python = resolve_python(config.workspace.connect_root, config.workspace.python).path
    result = await run_fixed_command(
        [
            str(python),
            "-m",
            "expra_connect_dev_mcp.performance_worker",
            action,
        ],
        cwd=config.workspace.connect_root,
        timeout_seconds=config.execution.command_timeout_seconds,
        max_output_kb=config.execution.max_output_kb,
    )
    if result.exit_code != 0:
        return PerformanceAuditResult(
            action=cast(PerformanceAction, action),
            status="FAIL",
            execution_mode="LOOPBACK_EXECUTION",
            retry=RetryEvidence(
                attempts=[],
                delays_seconds=[],
                authentication_disables_retry=False,
                cadence_source="unavailable",
            ),
            retention=RetentionEvidence(
                threads_before=0,
                threads_after=0,
                tasks_before=0,
                tasks_after=0,
                sockets_before=0,
                sockets_after=0,
                registry_before=0,
                registry_after=0,
                worker_processes_reaped=False,
                bounded=False,
            ),
            reasons=["configured Python performance worker failed"],
        )
    try:
        payload: dict[str, Any] = json.loads(result.stdout)
        return PerformanceAuditResult.model_validate(payload)
    except (json.JSONDecodeError, ValueError) as error:
        raise ToolError(
            f"performance worker returned invalid evidence: {type(error).__name__}"
        ) from error

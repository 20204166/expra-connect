"""Canonical cluster membership and failover evidence adapter."""

from __future__ import annotations

import json
from typing import Any, Literal, cast

from mcp.server.mcpserver.exceptions import ToolError

from .command_runner import run_fixed_command
from .config import McpConfig, resolve_python
from .models import ClusterAuditResult

ClusterAction = Literal["inspect", "failover"]


async def cluster_audit(
    config: McpConfig, action: str = "inspect"
) -> ClusterAuditResult:
    if action not in {"inspect", "failover"}:
        raise ToolError("unsupported cluster audit action")
    if action == "failover" and not config.execution.allow_cluster_mutation:
        raise ToolError(
            "cluster failover scenarios require execution.allow_cluster_mutation=true"
        )
    python = resolve_python(config.workspace.connect_root, config.workspace.python).path
    result = await run_fixed_command(
        [
            str(python),
            "-m",
            "expra_connect_dev_mcp.cluster_worker",
            action,
        ],
        cwd=config.workspace.connect_root,
        timeout_seconds=config.execution.command_timeout_seconds,
        max_output_kb=config.execution.max_output_kb,
    )
    if result.exit_code != 0:
        return ClusterAuditResult(
            action=cast(ClusterAction, action),
            status="FAIL",
            execution_mode="LOOPBACK_EXECUTION",
            cluster_id="unavailable",
            coordinator_id="unavailable",
            local_role="unknown",
            epoch=0,
            fencing_token_present=False,
            fencing_token_values_omitted=True,
            persisted_round_trip=False,
            reasons=["configured Python cluster worker failed"],
        )
    try:
        payload: dict[str, Any] = json.loads(result.stdout)
        return ClusterAuditResult.model_validate(payload)
    except (json.JSONDecodeError, ValueError) as error:
        return ClusterAuditResult(
            action=cast(ClusterAction, action),
            status="FAIL",
            execution_mode="LOOPBACK_EXECUTION",
            cluster_id="invalid",
            coordinator_id="unknown",
            local_role="unknown",
            epoch=0,
            fencing_token_present=False,
            fencing_token_values_omitted=True,
            persisted_round_trip=False,
            reasons=["cluster worker returned invalid evidence", type(error).__name__],
        )

"""Permission-gated capability and authorization security audit."""

from __future__ import annotations

import json
from typing import Any, Literal, cast

from mcp.server.mcpserver.exceptions import ToolError

from .command_runner import run_fixed_command
from .config import McpConfig, resolve_python
from .models import SecurityAuditResult

AuditAction = Literal[
    "all",
    "wrong_fingerprint",
    "revoked_peer",
    "unauthorized_capability",
    "stale_transaction",
]
_ACTIONS = {
    "all",
    "wrong_fingerprint",
    "revoked_peer",
    "unauthorized_capability",
    "stale_transaction",
}


async def security_audit(config: McpConfig, action: str = "all") -> SecurityAuditResult:
    if action not in _ACTIONS:
        raise ToolError("unsupported security audit action")
    if action != "stale_transaction" and not config.execution.allow_pairing:
        raise ToolError(
            "security network scenarios require execution.allow_pairing=true"
        )
    if action != "stale_transaction" and not config.execution.allow_trust_mutation:
        raise ToolError(
            "security network scenarios require execution.allow_trust_mutation=true"
        )
    if action == "stale_transaction" and not config.execution.allow_pairing:
        raise ToolError("stale transaction audit requires execution.allow_pairing=true")

    python = resolve_python(config.workspace.connect_root, config.workspace.python).path
    result = await run_fixed_command(
        [
            str(python),
            "-m",
            "expra_connect_dev_mcp.security_worker",
            action,
        ],
        cwd=config.workspace.connect_root,
        timeout_seconds=config.execution.command_timeout_seconds,
        max_output_kb=config.execution.max_output_kb,
    )
    if result.exit_code != 0:
        return SecurityAuditResult(
            action=cast(AuditAction, action),
            status="FAIL",
            execution_mode="LOOPBACK_EXECUTION",
            reasons=["configured Python security worker failed"],
        )
    try:
        payload: dict[str, Any] = json.loads(result.stdout)
        return SecurityAuditResult.model_validate(payload)
    except (json.JSONDecodeError, ValueError) as error:
        return SecurityAuditResult(
            action=cast(AuditAction, action),
            status="FAIL",
            execution_mode="LOOPBACK_EXECUTION",
            reasons=["security worker returned invalid evidence", type(error).__name__],
        )

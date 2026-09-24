"""Permission-gated multi-node scenarios executed outside the MCP process."""

from __future__ import annotations

import json
from typing import Any

from mcp.server.mcpserver.exceptions import ToolError

from .command_runner import run_fixed_command
from .config import McpConfig, resolve_python
from .models import ScenarioResult

_SCENARIOS = {"pair_reconnect", "endpoint_change", "peer_restart"}


async def multi_node_scenario(
    config: McpConfig, scenario: str = "pair_reconnect"
) -> ScenarioResult:
    if scenario not in _SCENARIOS:
        raise ToolError("unsupported multi-node scenario")
    if not config.execution.allow_pairing:
        raise ToolError(
            "multi-node scenarios require execution.allow_pairing=true; "
            "they create ephemeral pairing state"
        )
    if not config.execution.allow_trust_mutation:
        raise ToolError(
            "multi-node scenarios require execution.allow_trust_mutation=true; "
            "they persist ephemeral trust state"
        )
    python = resolve_python(config.workspace.connect_root, config.workspace.python).path
    result = await run_fixed_command(
        [
            str(python),
            "-m",
            "expra_connect_dev_mcp.scenario_worker",
            scenario,
        ],
        cwd=config.workspace.connect_root,
        timeout_seconds=config.execution.command_timeout_seconds,
        max_output_kb=config.execution.max_output_kb,
    )
    if result.exit_code != 0:
        return ScenarioResult(
            scenario=scenario,  # type: ignore[arg-type]
            status="FAIL",
            execution_mode="LOOPBACK_EXECUTION",
            reasons=["configured Python scenario worker failed"],
        )
    try:
        payload: dict[str, Any] = json.loads(result.stdout)
        return ScenarioResult.model_validate(payload)
    except (json.JSONDecodeError, ValueError) as error:
        return ScenarioResult(
            scenario=scenario,  # type: ignore[arg-type]
            status="FAIL",
            execution_mode="LOOPBACK_EXECUTION",
            reasons=["scenario worker returned invalid evidence", type(error).__name__],
        )

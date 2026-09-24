"""Permission-gated discovery, transport, and connection probes."""

from __future__ import annotations

import ipaddress
import json
from typing import Any, Literal, cast

from mcp.server.mcpserver.exceptions import ToolError

from .command_runner import run_fixed_command
from .config import McpConfig, resolve_python
from .models import ProbeResult, ProbeStage

ProbeName = Literal["discovery", "transport", "connection"]


def _is_loopback(host: str) -> bool:
    if host.lower() == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


async def _worker(
    config: McpConfig,
    probe: str,
    action: str,
    *,
    host: str = "127.0.0.1",
    port: int = 0,
    expected_fingerprint: str | None = None,
) -> ProbeResult:
    python = resolve_python(config.workspace.connect_root, config.workspace.python).path
    command = [
        str(python),
        "-m",
        "expra_connect_dev_mcp.runtime_worker",
        probe,
        "connect_loopback" if action == "connect_peer" else action,
        host,
        str(port),
        expected_fingerprint or "",
    ]
    result = await run_fixed_command(
        command,
        cwd=config.workspace.connect_root,
        timeout_seconds=config.execution.command_timeout_seconds,
        max_output_kb=config.execution.max_output_kb,
    )
    if result.exit_code != 0:
        return ProbeResult(
            probe=cast(ProbeName, probe),
            action=action,
            status="FAIL",
            execution_mode="STATIC",
            stages=[
                ProbeStage(stage="WORKER", outcome="FAIL", detail=result.stderr.strip())
            ],
            reasons=["configured Python worker failed"],
        )
    try:
        payload: dict[str, Any] = json.loads(result.stdout)
        if action == "connect_peer" and _is_loopback(host) is False:
            payload["execution_mode"] = "LAN_EXECUTION"
        return ProbeResult.model_validate(payload)
    except (json.JSONDecodeError, ValueError) as error:
        return ProbeResult(
            probe=cast(ProbeName, probe),
            action=action,
            status="FAIL",
            execution_mode="STATIC",
            stages=[
                ProbeStage(stage="WORKER_OUTPUT", outcome="FAIL", detail=str(error))
            ],
            reasons=["configured Python worker returned invalid evidence"],
        )


async def discovery_probe(
    config: McpConfig, action: str = "local_loopback"
) -> ProbeResult:
    if action == "live_discovery" and not config.execution.allow_lan_discovery:
        raise ToolError("live_discovery requires execution.allow_lan_discovery=true")
    if action not in {"local_loopback", "advertisement", "live_discovery"}:
        raise ToolError("unsupported discovery action")
    return await _worker(config, "discovery", action)


async def transport_probe(
    config: McpConfig,
    action: str = "listen_state",
    host: str = "127.0.0.1",
    port: int = 0,
    expected_fingerprint: str | None = None,
) -> ProbeResult:
    if action in {"connect_loopback", "connect_peer"}:
        loopback = _is_loopback(host)
        if action == "connect_loopback" and not loopback:
            raise ToolError("connect_loopback only accepts loopback hosts")
        if not loopback and not config.execution.allow_network:
            raise ToolError("connect_peer requires execution.allow_network=true")
        if port < 0 or port > 65535:
            raise ToolError("port must be between 0 and 65535")
    elif action not in {
        "config",
        "listen_state",
        "tls_state",
        "loopback_handshake",
    }:
        raise ToolError("unsupported transport action")
    return await _worker(
        config,
        "transport",
        action,
        host=host,
        port=port,
        expected_fingerprint=expected_fingerprint,
    )


async def connection_probe(config: McpConfig, action: str = "state") -> ProbeResult:
    if action not in {"state", "retry_state", "offline_scenario"}:
        raise ToolError("unsupported connection action")
    if action != "state":
        result = await _worker(config, "connection", "state")
        result.action = action
        result.reasons.append("scenario execution is not enabled in Phase 2D")
        result.status = "PARTIAL"
        return result
    return await _worker(config, "connection", action)

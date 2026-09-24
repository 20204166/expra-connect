"""Official MCP protocol surface for the development server."""

from __future__ import annotations

import json

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ResourceError, ToolError
from mcp.types import PromptMessage, TextContent, ToolAnnotations

from . import __version__
from .checks import run_check
from .config import McpConfig
from .connect_adapter import inspect_connect
from .doctor import workspace_doctor
from .models import (
    CheckResult,
    ClusterAuditResult,
    InspectionResult,
    PerformanceAuditResult,
    ProbeResult,
    RepoInspectResult,
    ScenarioResult,
    SecurityAuditResult,
    SourceReadResult,
    SourceSearchResult,
    WorkspaceDoctorResult,
)
from .phase2d import connection_probe, discovery_probe, transport_probe
from .phase2e import multi_node_scenario
from .phase2f import security_audit
from .phase2g import cluster_audit
from .phase2h import performance_audit
from .source_access import inspect_repo, read_source, search_source

_READ_ONLY = ToolAnnotations(
    read_only_hint=True,
    destructive_hint=False,
    idempotent_hint=True,
    open_world_hint=False,
)

_SCENARIO = ToolAnnotations(
    read_only_hint=False,
    destructive_hint=False,
    idempotent_hint=True,
    open_world_hint=False,
)


def build_server(config: McpConfig) -> MCPServer:
    server = MCPServer(
        name="expra-connect-dev-mcp",
        version=__version__,
        description="Evidence and controlled checks for Expra Connect development.",
    )

    @server.tool(
        name="workspace_doctor",
        description="Prove the configured Expra Connect source and Python environment.",
        annotations=_READ_ONLY,
        structured_output=True,
    )
    async def workspace_doctor_tool() -> WorkspaceDoctorResult:
        return await workspace_doctor(config)

    @server.tool(
        name="source_read",
        description="Read one UTF-8 source file from a configured read-only root.",
        annotations=_READ_ONLY,
        structured_output=True,
    )
    def source_read_tool(source_id: str, path: str) -> SourceReadResult:
        try:
            return read_source(
                config,
                source_id,
                path,
                max_output_kb=config.execution.max_output_kb,
            )
        except (OSError, ValueError) as error:
            raise ToolError(str(error)) from error

    @server.tool(
        name="source_search",
        description="Search a configured read-only source root with bounded results.",
        annotations=_READ_ONLY,
        structured_output=True,
    )
    async def source_search_tool(
        source_id: str, query: str, path: str = "."
    ) -> SourceSearchResult:
        try:
            return await search_source(
                config,
                source_id,
                query,
                path,
                max_output_kb=config.execution.max_output_kb,
            )
        except (OSError, ValueError, RuntimeError) as error:
            raise ToolError(str(error)) from error

    @server.tool(
        name="repo_inspect",
        description="Inspect repository shape and git state without executing project code.",
        annotations=_READ_ONLY,
        structured_output=True,
    )
    async def repo_inspect_tool() -> RepoInspectResult:
        return await inspect_repo(config)

    @server.tool(
        name="run_checks",
        description="Run one named repository check profile; arbitrary commands are rejected.",
        annotations=_READ_ONLY,
        structured_output=True,
    )
    async def run_checks_tool(profile: str) -> CheckResult:
        try:
            return await run_check(config, profile)
        except (OSError, ValueError, RuntimeError) as error:
            raise ToolError(str(error)) from error

    @server.tool(
        name="connect_inspect",
        description="Inspect current Connect architecture and static module evidence.",
        annotations=_READ_ONLY,
        structured_output=True,
    )
    async def connect_inspect_tool(action: str = "architecture") -> InspectionResult:
        return await inspect_connect(config, action)

    @server.tool(
        name="identity_inspect",
        description="Inspect persisted identity metadata without returning private keys.",
        annotations=_READ_ONLY,
        structured_output=True,
    )
    async def identity_inspect_tool() -> InspectionResult:
        return await inspect_connect(config, "identity")

    @server.tool(
        name="registry_inspect",
        description="Inspect persisted registry-related evidence without executing runtime code.",
        annotations=_READ_ONLY,
        structured_output=True,
    )
    async def registry_inspect_tool() -> InspectionResult:
        return await inspect_connect(config, "registry")

    @server.tool(
        name="pairing_inspect",
        description="Inspect pairing and trust state with secret material redacted.",
        annotations=_READ_ONLY,
        structured_output=True,
    )
    async def pairing_inspect_tool() -> InspectionResult:
        return await inspect_connect(config, "pairing")

    @server.tool(
        name="capability_inspect",
        description="Inspect capability implementation and persisted grant evidence.",
        annotations=_READ_ONLY,
        structured_output=True,
    )
    async def capability_inspect_tool() -> InspectionResult:
        return await inspect_connect(config, "capability")

    @server.tool(
        name="cluster_inspect",
        description="Inspect cluster implementation and local persisted membership evidence.",
        annotations=_READ_ONLY,
        structured_output=True,
    )
    async def cluster_inspect_tool() -> InspectionResult:
        return await inspect_connect(config, "cluster")

    @server.tool(
        name="discovery_probe",
        description="Probe discovery evidence; local loopback is default and LAN discovery is gated.",
        annotations=_READ_ONLY,
        structured_output=True,
    )
    async def discovery_probe_tool(action: str = "local_loopback") -> ProbeResult:
        return await discovery_probe(config, action)

    @server.tool(
        name="transport_probe",
        description="Separate listener, TCP, TLS, fingerprint, and authentication stages.",
        annotations=_READ_ONLY,
        structured_output=True,
    )
    async def transport_probe_tool(
        action: str = "listen_state",
        host: str = "127.0.0.1",
        port: int = 0,
        expected_fingerprint: str | None = None,
    ) -> ProbeResult:
        return await transport_probe(config, action, host, port, expected_fingerprint)

    @server.tool(
        name="connection_probe",
        description="Inspect connection state and distinguish unavailable retry evidence.",
        annotations=_READ_ONLY,
        structured_output=True,
    )
    async def connection_probe_tool(action: str = "state") -> ProbeResult:
        return await connection_probe(config, action)

    @server.tool(
        name="multi_node_scenario",
        description=(
            "Run an isolated two-node loopback scenario for pairing, reconnect, "
            "endpoint change, or peer restart; explicit pairing and trust gates apply."
        ),
        annotations=_SCENARIO,
        structured_output=True,
    )
    async def multi_node_scenario_tool(
        scenario: str = "pair_reconnect",
    ) -> ScenarioResult:
        return await multi_node_scenario(config, scenario)

    @server.tool(
        name="security_audit",
        description=(
            "Exercise capability and authorization boundaries for wrong fingerprints, "
            "revoked peers, unauthorized capabilities, and stale transactions."
        ),
        annotations=_SCENARIO,
        structured_output=True,
    )
    async def security_audit_tool(action: str = "all") -> SecurityAuditResult:
        return await security_audit(config, action)

    @server.tool(
        name="cluster_audit",
        description=(
            "Inspect canonical cluster membership, roles, coordinator epoch, and "
            "fencing evidence, or run an actual failover transition scenario."
        ),
        annotations=_SCENARIO,
        structured_output=True,
    )
    async def cluster_audit_tool(action: str = "inspect") -> ClusterAuditResult:
        return await cluster_audit(config, action)

    @server.tool(
        name="performance_audit",
        description=(
            "Measure loopback discovery, connect, and reconnect latency; report "
            "retry cadence and bounded thread, task, socket, registry, and worker retention."
        ),
        annotations=_READ_ONLY,
        structured_output=True,
    )
    async def performance_audit_tool(
        action: str = "all",
    ) -> PerformanceAuditResult:
        return await performance_audit(config, action)

    @server.resource(
        "source://connect/{path}",
        name="connect-source",
        description="Read-only Expra Connect source file.",
        mime_type="text/plain",
    )
    def connect_source_resource(path: str) -> str:
        try:
            return read_source(
                config, "connect", path, max_output_kb=config.execution.max_output_kb
            ).content
        except (OSError, ValueError) as error:
            raise ResourceError(str(error)) from error

    @server.resource(
        "source://exp_ui/{path}",
        name="exp-ui-source",
        description="Read-only reference source file.",
        mime_type="text/plain",
    )
    def exp_ui_source_resource(path: str) -> str:
        try:
            return read_source(
                config, "exp_ui", path, max_output_kb=config.execution.max_output_kb
            ).content
        except (OSError, ValueError) as error:
            raise ResourceError(str(error)) from error

    @server.resource(
        "connect://workspace/doctor",
        name="workspace-doctor-resource",
        description="Current workspace doctor evidence.",
        mime_type="application/json",
    )
    async def doctor_resource() -> str:
        return json.dumps((await workspace_doctor(config)).model_dump(), sort_keys=True)

    @server.resource(
        "connect://architecture",
        name="architecture-resource",
        description="Canonical current architecture contract.",
        mime_type="text/markdown",
    )
    def architecture_resource() -> str:
        return read_source(
            config,
            "connect",
            "docs/ARCHITECTURE.md",
            max_output_kb=config.execution.max_output_kb,
        ).content

    @server.resource(
        "connect://security-model",
        name="security-model-resource",
        description="Canonical current security contract.",
        mime_type="text/markdown",
    )
    def security_resource() -> str:
        return read_source(
            config,
            "connect",
            "docs/SECURITY_MODEL.md",
            max_output_kb=config.execution.max_output_kb,
        ).content

    _add_prompts(server)
    return server


def _add_prompts(server: MCPServer) -> None:
    def prompt(text: str) -> list[PromptMessage]:
        return [PromptMessage(role="user", content=TextContent(text=text))]

    @server.prompt("debug-discovery", description="Debug discovery in evidence order.")
    def debug_discovery() -> list[PromptMessage]:
        return prompt(
            "Use workspace_doctor, repo_inspect, source_search, and source_read to prove discovery configuration and current implementation before attempting live activity."
        )

    @server.prompt(
        "debug-connection", description="Debug a connection one stage at a time."
    )
    def debug_connection() -> list[PromptMessage]:
        return prompt(
            "Work from identity to discovery, registry, pairing, trust, transport, authorization, and capability state. Report the first failed stage with direct evidence; do not infer offline from a generic error."
        )

    @server.prompt(
        "audit-security", description="Audit the current local security contract."
    )
    def audit_security() -> list[PromptMessage]:
        return prompt(
            "Read SECURITY_MODEL.md and the relevant current source. Separate IMPLEMENTED, PARTIAL, TEST-ONLY, DEFERRED, and NOT PRESENT evidence. Never expose secrets."
        )

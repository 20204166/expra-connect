from __future__ import annotations

import asyncio
import os
import socket
import sys
import unittest
from dataclasses import replace
from pathlib import Path

from mcp import Client
from mcp.server.mcpserver import MCPServer

from expra_connect_dev_mcp.config import default_config
from expra_connect_dev_mcp.server import build_server


class ProtocolTests(unittest.IsolatedAsyncioTestCase):
    @staticmethod
    def _environment() -> dict[str, str]:
        environment = os.environ.copy()
        source = str(Path(__file__).resolve().parents[1] / "src")
        current = environment.get("PYTHONPATH")
        environment["PYTHONPATH"] = (
            source if not current else source + os.pathsep + current
        )
        return environment

    async def test_in_process_client_sees_tools_resources_prompts_and_structured_doctor(
        self,
    ) -> None:
        server: MCPServer = build_server(default_config())
        async with Client(server) as client:
            tools = await client.list_tools()
            resources = await client.list_resources()
            templates = await client.list_resource_templates()
            prompts = await client.list_prompts()
            result = await client.call_tool("workspace_doctor")

            self.assertIn("workspace_doctor", {tool.name for tool in tools.tools})
            self.assertTrue(resources.resources)
            self.assertTrue(templates.resource_templates)
            self.assertIn(
                "debug-discovery", {prompt.name for prompt in prompts.prompts}
            )
            self.assertFalse(result.is_error)
            self.assertIsNotNone(result.structured_content)
            self.assertIn("status", result.structured_content or {})
            for tool_name in (
                "connect_inspect",
                "identity_inspect",
                "registry_inspect",
                "pairing_inspect",
                "capability_inspect",
                "cluster_inspect",
            ):
                inspection = await client.call_tool(tool_name)
                self.assertFalse(inspection.is_error, tool_name)
                self.assertIn("status", inspection.structured_content or {})
            self.assertIn("multi_node_scenario", {tool.name for tool in tools.tools})
            self.assertIn("security_audit", {tool.name for tool in tools.tools})
            self.assertIn("cluster_audit", {tool.name for tool in tools.tools})
            self.assertIn("performance_audit", {tool.name for tool in tools.tools})
            discovery = await client.call_tool("discovery_probe")
            transport = await client.call_tool("transport_probe")
            connection = await client.call_tool("connection_probe")
            self.assertEqual(discovery.structured_content["status"], "PASS")
            self.assertEqual(transport.structured_content["status"], "PASS")
            self.assertEqual(connection.structured_content["status"], "PASS")
            self.assertEqual(
                discovery.structured_content["execution_mode"], "LOOPBACK_EXECUTION"
            )
            handshake = await client.call_tool(
                "transport_probe", {"action": "loopback_handshake"}
            )
            self.assertEqual(handshake.structured_content["status"], "PASS")
            self.assertEqual(
                handshake.structured_content["stages"][1]["stage"],
                "TLS_HANDSHAKE",
            )
            source = await client.call_tool(
                "source_read", {"source_id": "connect", "path": "docs/ARCHITECTURE.md"}
            )
            search = await client.call_tool(
                "source_search",
                {"source_id": "connect", "query": "ConnectRuntime", "path": "src"},
            )
            self.assertIn("Architecture", str(source.structured_content))
            self.assertTrue(search.structured_content)
            architecture = await client.read_resource("connect://architecture")
            prompt = await client.get_prompt("debug-discovery")
            self.assertTrue(architecture.contents)
            self.assertIn("workspace_doctor", str(prompt.messages[0].content))

    async def test_transport_rejects_non_loopback_without_network_permission(
        self,
    ) -> None:
        server = build_server(default_config())
        async with Client(server) as client:
            result = await client.call_tool(
                "transport_probe",
                {"action": "connect_peer", "host": "192.0.2.10", "port": 27321},
            )

            self.assertTrue(result.is_error)

    async def test_transport_reports_port_zero_as_endpoint_failure(self) -> None:
        server = build_server(default_config())
        async with Client(server) as client:
            result = await client.call_tool(
                "transport_probe",
                {"action": "connect_loopback", "host": "127.0.0.1", "port": 0},
            )

            self.assertFalse(result.is_error)
            self.assertEqual(result.structured_content["status"], "FAIL")
            self.assertEqual(
                result.structured_content["stages"][0]["stage"],
                "TRANSPORT_ENDPOINT_INVALID",
            )

    async def test_multi_node_scenarios_require_explicit_mutation_permissions(
        self,
    ) -> None:
        server = build_server(default_config())
        async with Client(server) as client:
            result = await client.call_tool("multi_node_scenario")

            self.assertTrue(result.is_error)

    async def test_multi_node_scenarios_run_in_isolated_children(self) -> None:
        config = default_config()
        config = replace(
            config,
            execution=replace(
                config.execution,
                allow_pairing=True,
                allow_trust_mutation=True,
            ),
        )
        server = build_server(config)
        async with Client(server) as client:
            for scenario in ("pair_reconnect", "endpoint_change", "peer_restart"):
                result = await client.call_tool(
                    "multi_node_scenario", {"scenario": scenario}
                )

                self.assertFalse(result.is_error, scenario)
                evidence = result.structured_content or {}
                self.assertEqual(evidence["status"], "PASS")
                self.assertEqual(evidence["execution_mode"], "LOOPBACK_EXECUTION")
                self.assertGreaterEqual(len(evidence["timeline"]), 10)
                self.assertEqual(
                    evidence["timeline"][-1]["event"], "reconnect_succeeded"
                )
                serialized = str(evidence)
                for forbidden in (
                    "transport_fingerprint",
                    "transport_proof",
                    "root_private_key",
                    "secret",
                ):
                    self.assertNotIn(forbidden, serialized)

    async def test_security_audit_requires_mutation_permissions(self) -> None:
        server = build_server(default_config())
        async with Client(server) as client:
            result = await client.call_tool("security_audit")

            self.assertTrue(result.is_error)

    async def test_security_audit_exercises_denial_boundaries(self) -> None:
        config = default_config()
        config = replace(
            config,
            execution=replace(
                config.execution,
                allow_pairing=True,
                allow_trust_mutation=True,
            ),
        )
        server = build_server(config)
        async with Client(server) as client:
            result = await client.call_tool("security_audit")

            self.assertFalse(result.is_error)
            evidence = result.structured_content or {}
            self.assertEqual(evidence["status"], "PASS")
            self.assertEqual(len(evidence["findings"]), 4)
            self.assertTrue(
                all(item["observed_denial"] for item in evidence["findings"])
            )
            self.assertEqual(
                evidence["findings"][2]["observed_capabilities"], ["read_state"]
            )
            self.assertEqual(
                [event["sequence"] for event in evidence["timeline"]],
                list(range(1, len(evidence["timeline"]) + 1)),
            )
            serialized = str(evidence)
            for forbidden in ("secret", "root_private_key", "transport_proof"):
                self.assertNotIn(forbidden, serialized)

    async def test_cluster_audit_reports_canonical_membership_and_fencing_presence(
        self,
    ) -> None:
        server = build_server(default_config())
        async with Client(server) as client:
            result = await client.call_tool("cluster_audit")

            self.assertFalse(result.is_error)
            evidence = result.structured_content or {}
            self.assertEqual(evidence["status"], "PASS")
            self.assertTrue(evidence["persisted_round_trip"])
            self.assertTrue(evidence["fencing_token_present"])
            self.assertTrue(evidence["fencing_token_values_omitted"])
            self.assertEqual(
                {member["node_id"] for member in evidence["members"]},
                {"coordinator"},
            )

    async def test_cluster_failover_requires_explicit_cluster_permission(self) -> None:
        server = build_server(default_config())
        async with Client(server) as client:
            result = await client.call_tool("cluster_audit", {"action": "failover"})

            self.assertTrue(result.is_error)

    async def test_cluster_failover_reports_epoch_and_role_transition(self) -> None:
        config = default_config()
        config = replace(
            config,
            execution=replace(config.execution, allow_cluster_mutation=True),
        )
        server = build_server(config)
        async with Client(server) as client:
            result = await client.call_tool("cluster_audit", {"action": "failover"})

            self.assertFalse(result.is_error)
            evidence = result.structured_content or {}
            failover = evidence["failover"]
            self.assertEqual(evidence["status"], "PASS")
            self.assertEqual(failover["old_epoch"] + 1, failover["new_epoch"])
            self.assertTrue(failover["stale_heartbeat_rejected"])
            self.assertTrue(failover["duplicate_promotion_rejected"])
            self.assertEqual(failover["returning_coordinator_role"], "worker")
            self.assertTrue(failover["fencing_token_values_omitted"])
            serialized = str(evidence)
            self.assertNotIn("'fencing_token':", serialized)
            self.assertNotIn("'token':", serialized)

    async def test_performance_audit_aggregates_latency_retry_and_retention(
        self,
    ) -> None:
        server = build_server(default_config())
        async with Client(server) as client:
            result = await client.call_tool("performance_audit")

            self.assertFalse(result.is_error)
            evidence = result.structured_content or {}
            self.assertEqual(evidence["status"], "PASS")
            self.assertEqual(
                {item["name"] for item in evidence["latencies"]},
                {"discovery", "connect", "reconnect"},
            )
            for item in evidence["latencies"]:
                self.assertGreaterEqual(item["sample_count"], 3)
                self.assertIn("p50", item["distribution"])
                self.assertIn("p99", item["distribution"])
            self.assertEqual(evidence["retry"]["delays_seconds"], [1.0, 2.0, 4.0])
            self.assertTrue(evidence["retry"]["authentication_disables_retry"])
            self.assertTrue(evidence["retention"]["bounded"])
            self.assertTrue(evidence["retention"]["worker_processes_reaped"])

    async def test_stdio_subprocess_is_protocol_only(self) -> None:
        from mcp.client.stdio import StdioServerParameters

        parameters = StdioServerParameters(
            command=sys.executable,
            args=["-m", "expra_connect_dev_mcp", "--transport", "stdio"],
            cwd=str(Path(__file__).resolve().parents[3]),
            env=self._environment(),
        )
        async with Client(parameters) as client:
            result = await client.call_tool("workspace_doctor")
            self.assertFalse(result.is_error)
            self.assertIn("status", result.structured_content or {})

    async def test_path_escape_is_a_tool_error(self) -> None:
        server = build_server(default_config())
        async with Client(server) as client:
            result = await client.call_tool(
                "source_read", {"source_id": "connect", "path": "../AGENTS.md"}
            )

            self.assertTrue(result.is_error)

    async def test_streamable_http_subprocess_is_reachable_on_loopback(self) -> None:
        from mcp import Client

        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            port = listener.getsockname()[1]
        process = await asyncio.create_subprocess_exec(
            sys.executable,
            "-m",
            "expra_connect_dev_mcp",
            "--transport",
            "streamable-http",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
            cwd=str(Path(__file__).resolve().parents[3]),
            env=self._environment(),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            for _ in range(50):
                await asyncio.sleep(0.1)
                if process.returncode is not None:
                    break
                try:
                    with socket.create_connection(("127.0.0.1", port), timeout=0.1):
                        break
                except OSError:
                    continue
            async with Client(f"http://127.0.0.1:{port}/mcp") as client:
                result = await client.call_tool("workspace_doctor")
                self.assertFalse(result.is_error)
                self.assertIn("status", result.structured_content or {})
        finally:
            if process.returncode is None:
                process.terminate()
            await process.wait()


if __name__ == "__main__":
    unittest.main()

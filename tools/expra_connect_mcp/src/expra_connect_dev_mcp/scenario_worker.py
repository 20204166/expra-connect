"""Orchestrate isolated child Connect nodes for Phase 2E scenarios."""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any


class NodeProcess:
    def __init__(self, profile: Path, name: str) -> None:
        self.name = name
        self.process = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "expra_connect_dev_mcp.node_worker",
                str(profile),
                name,
            ],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            cwd=Path.cwd(),
        )

    def request(
        self, command: str, payload: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        if self.process.stdin is None or self.process.stdout is None:
            raise RuntimeError("node worker pipes are unavailable")
        self.process.stdin.write(
            json.dumps({"command": command, "payload": payload or {}}) + "\n"
        )
        self.process.stdin.flush()
        line = self.process.stdout.readline()
        if not line:
            raise RuntimeError("node worker exited without a response")
        response: dict[str, Any] = json.loads(line)
        if not response.get("ok"):
            raise RuntimeError(str(response.get("error_type", "node worker failed")))
        return response

    def close(self) -> None:
        if self.process.poll() is None:
            try:
                self.request("shutdown")
            except (OSError, RuntimeError, ValueError):
                pass
            self.process.terminate()
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait()


class Scenario:
    def __init__(self, name: str, root: Path) -> None:
        self.name = name
        self.timeline: list[dict[str, Any]] = []
        self.advertisements: dict[str, dict[str, Any]] = {}
        self.nodes = {
            "node_a": NodeProcess(root / "node-a", "node-a"),
            "node_b": NodeProcess(root / "node-b", "node-b"),
        }

    def event(
        self, node: str, event: str, outcome: str = "PASS", detail: str | None = None
    ) -> None:
        value: dict[str, Any] = {
            "sequence": len(self.timeline) + 1,
            "node": node,
            "event": event,
            "outcome": outcome,
        }
        if detail is not None:
            value["detail"] = detail
        self.timeline.append(value)

    def request(
        self,
        node: str,
        command: str,
        payload: dict[str, Any] | None = None,
        event: str | None = None,
    ) -> dict[str, Any]:
        response = self.nodes[node].request(command, payload)
        if event is not None:
            self.event(node, event)
        return response

    def start(self) -> None:
        for name in self.nodes:
            response = self.request(name, "start", event="node_started")
            self.advertisements[name] = response["advertisement"]

    def refresh_advertisement(self, name: str) -> None:
        self.advertisements[name] = self.nodes[name].request("advertisement")[
            "advertisement"
        ]

    def inject(self, target: str, source: str) -> None:
        self.request(
            target,
            "inject_candidate",
            self.advertisements[source],
            event="candidate_observed",
        )

    def action(
        self,
        node: str,
        command: str,
        payload: dict[str, Any] | None,
        started: str,
        succeeded: str,
        success_detail: str | None = None,
    ) -> dict[str, Any]:
        self.event(node, started)
        try:
            response = self.request(node, command, payload)
        except Exception as error:
            self.event(node, succeeded, "FAIL", type(error).__name__)
            raise
        self.event(node, succeeded, detail=success_detail)
        return response

    def run(self) -> dict[str, Any]:
        self.start()
        self.inject("node_a", "node_b")
        peer_id = self.advertisements["node_b"]["node_id"]
        self.action(
            "node_a",
            "pair",
            {"peer_id": peer_id},
            "pairing_started",
            "pairing_succeeded",
        )
        self.action(
            "node_a",
            "connect",
            {"peer_id": peer_id},
            "connect_started",
            "connection_succeeded",
        )
        if self.name == "pair_reconnect":
            self.action(
                "node_a",
                "disconnect",
                {"peer_id": peer_id},
                "disconnect_started",
                "disconnect_succeeded",
            )
            self.action(
                "node_a",
                "reconnect",
                {"peer_id": peer_id},
                "reconnect_started",
                "reconnect_succeeded",
            )
        elif self.name == "endpoint_change":
            old_port = self.nodes["node_b"].request("state")["state"]["bound_port"]
            response = self.action(
                "node_b", "rotate", None, "endpoint_change_started", "endpoint_changed"
            )
            new_port = response["state"]["bound_port"]
            if old_port == new_port:
                raise RuntimeError("endpoint did not change")
            self.timeline[-1]["detail"] = "listener endpoint changed"
            self.refresh_advertisement("node_b")
            self.inject("node_a", "node_b")
            self.action(
                "node_a",
                "disconnect",
                {"peer_id": peer_id},
                "disconnect_started",
                "disconnect_succeeded",
            )
            self.action(
                "node_a",
                "reconnect",
                {"peer_id": peer_id},
                "reconnect_started",
                "reconnect_succeeded",
            )
        elif self.name == "peer_restart":
            self.action(
                "node_b", "restart", None, "peer_restart_started", "peer_restarted"
            )
            self.refresh_advertisement("node_b")
            self.inject("node_a", "node_b")
            self.action(
                "node_a",
                "disconnect",
                {"peer_id": peer_id},
                "disconnect_started",
                "disconnect_succeeded",
            )
            self.action(
                "node_a",
                "reconnect",
                {"peer_id": peer_id},
                "reconnect_started",
                "reconnect_succeeded",
            )
        else:
            raise ValueError("unsupported scenario")
        nodes = [self.nodes[name].request("state")["state"] for name in self.nodes]
        return _scenario_result(self.name, "PASS", self.timeline, nodes, [])

    def close(self) -> None:
        for node in self.nodes.values():
            node.close()


def _scenario_result(
    scenario: str,
    status: str,
    timeline: list[dict[str, Any]],
    nodes: list[dict[str, Any]],
    reasons: list[str],
) -> dict[str, Any]:
    return {
        "scenario": scenario,
        "status": status,
        "execution_mode": "LOOPBACK_EXECUTION",
        "nodes": nodes,
        "timeline": timeline,
        "reasons": reasons,
        "executed_network_code": True,
        "executed_user_code": False,
    }


def execute(scenario: str) -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="expra-mcp-scenario-") as directory:
        controller = Scenario(scenario, Path(directory))
        try:
            return controller.run()
        except Exception as error:  # noqa: BLE001 - redact at scenario boundary.
            for name in controller.nodes:
                controller.event(name, "scenario_failed", "FAIL", type(error).__name__)
            return _scenario_result(
                scenario,
                "FAIL",
                controller.timeline,
                [],
                [type(error).__name__],
            )
        finally:
            controller.close()


def main() -> int:
    scenario = sys.argv[1]
    print(json.dumps(execute(scenario), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Run Phase 2F security checks against isolated child Connect nodes."""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path
from typing import Any, cast

from .scenario_worker import NodeProcess

_CASES = (
    "wrong_fingerprint",
    "revoked_peer",
    "unauthorized_capability",
    "stale_transaction",
)


class SecurityCase:
    def __init__(self, name: str, root: Path) -> None:
        self.name = name
        self.timeline: list[dict[str, Any]] = []
        self.sequence = 0
        self.nodes = {
            "node_a": NodeProcess(root / "node-a", "node-a"),
            "node_b": NodeProcess(root / "node-b", "node-b"),
        }
        self.advertisements: dict[str, dict[str, Any]] = {}

    def event(self, node: str, event: str, outcome: str = "PASS") -> None:
        self.sequence += 1
        self.timeline.append(
            {
                "sequence": self.sequence,
                "node": node,
                "event": event,
                "outcome": outcome,
            }
        )

    def request(
        self, node: str, command: str, payload: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        return self.nodes[node].request(command, payload)

    def setup_pair(self) -> str:
        for name in self.nodes:
            response = self.request(name, "start")
            self.advertisements[name] = response["advertisement"]
            self.event(name, "node_started")
        self.request("node_a", "inject_candidate", self.advertisements["node_b"])
        self.event("node_a", "candidate_observed")
        peer_id = cast(str, self.advertisements["node_b"]["node_id"])
        self.event("node_a", "pairing_started")
        self.request("node_a", "pair", {"peer_id": peer_id})
        self.event("node_a", "pairing_succeeded")
        self.event("node_a", "connect_started")
        self.request("node_a", "connect", {"peer_id": peer_id})
        self.event("node_a", "connection_succeeded")
        return peer_id

    def expect_denial(
        self, node: str, command: str, payload: dict[str, Any], expected: str
    ) -> bool:
        try:
            self.request(node, command, payload)
        except RuntimeError as error:
            return expected in str(error)
        return False

    def run(self) -> dict[str, Any]:
        if self.name == "stale_transaction":
            self.request("node_a", "start")
            self.event("node_a", "node_started")
            response = self.request(
                "node_a", "stale_transaction", {"peer_id": "stale-peer"}
            )
            denied = bool(response.get("rejected"))
            self.event(
                "node_a", "stale_transaction_rejected", "PASS" if denied else "FAIL"
            )
            return {
                "denied": denied,
                "observed_capabilities": [],
            }

        peer_id = self.setup_pair()
        if self.name == "wrong_fingerprint":
            self.request("node_a", "disconnect", {"peer_id": peer_id})
            bad = dict(self.advertisements["node_b"])
            bad["transport_fingerprint"] = "0" * 64
            self.request("node_a", "force_candidate", bad)
            denied = self.expect_denial(
                "node_a", "connect", {"peer_id": peer_id}, "RemoteAuthError"
            )
            self.event(
                "node_a", "wrong_fingerprint_rejected", "PASS" if denied else "FAIL"
            )
            return {"denied": denied, "observed_capabilities": []}
        if self.name == "revoked_peer":
            self.request("node_a", "revoke", {"peer_id": peer_id})
            self.request("node_a", "force_candidate", self.advertisements["node_b"])
            denied = self.expect_denial(
                "node_a", "connect", {"peer_id": peer_id}, "PermissionError"
            )
            self.event("node_a", "revoked_peer_rejected", "PASS" if denied else "FAIL")
            return {"denied": denied, "observed_capabilities": []}
        if self.name == "unauthorized_capability":
            response = self.request(
                "node_a",
                "capability_probe",
                {"peer_id": peer_id, "capability": "remote_management"},
            )
            denied = not bool(response.get("authorized"))
            capabilities = [
                str(item) for item in response.get("advertised_capabilities", [])
            ]
            self.event(
                "node_a",
                "unauthorized_capability_rejected",
                "PASS" if denied else "FAIL",
            )
            return {"denied": denied, "observed_capabilities": capabilities}
        raise ValueError("unsupported security case")

    def close(self) -> None:
        for node in self.nodes.values():
            node.close()


def _finding(name: str, case: SecurityCase) -> dict[str, Any]:
    try:
        result = case.run()
        denied = bool(result["denied"])
        status = "PASS" if denied else "FAIL"
        reasons = [] if denied else ["expected denial was not observed"]
        return {
            "name": name,
            "status": status,
            "expected_denial": True,
            "observed_denial": denied,
            "observed_capabilities": result["observed_capabilities"],
            "reasons": reasons,
        }
    except Exception as error:  # noqa: BLE001 - redact at scenario boundary.
        return {
            "name": name,
            "status": "FAIL",
            "expected_denial": True,
            "observed_denial": False,
            "observed_capabilities": [],
            "reasons": [type(error).__name__],
        }


def execute(action: str) -> dict[str, Any]:
    cases = _CASES if action == "all" else (action,)
    findings: list[dict[str, Any]] = []
    timeline: list[dict[str, Any]] = []
    reasons: list[str] = []
    for name in cases:
        with tempfile.TemporaryDirectory(prefix="expra-mcp-security-") as directory:
            case = SecurityCase(name, Path(directory))
            finding = _finding(name, case)
            findings.append(finding)
            offset = len(timeline)
            timeline.extend(
                {**event, "sequence": event["sequence"] + offset}
                for event in case.timeline
            )
            if finding["status"] != "PASS":
                reasons.extend(finding["reasons"])
            case.close()
    return {
        "action": action,
        "status": "PASS" if findings and not reasons else "FAIL",
        "execution_mode": "LOOPBACK_EXECUTION",
        "findings": findings,
        "timeline": timeline,
        "reasons": reasons,
        "executed_network_code": action != "stale_transaction",
        "executed_user_code": False,
    }


def main() -> int:
    print(json.dumps(execute(sys.argv[1]), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

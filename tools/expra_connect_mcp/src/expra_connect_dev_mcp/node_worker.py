"""Persistent one-runtime child process used by Phase 2E scenarios."""

from __future__ import annotations

import json
import os
import sys
import threading
from pathlib import Path
from typing import Any


def _candidate(raw: dict[str, Any]) -> Any:
    from expra_connect import DiscoveredNodeCandidate, EndpointCandidate

    endpoint = EndpointCandidate(raw["host"], int(raw["port"]))
    return DiscoveredNodeCandidate(
        stable_id=raw["node_id"],
        hostname=raw["node_id"],
        addresses=(raw["host"],),
        port=int(raw["port"]),
        service_name=f"{raw['node_id']}.local.",
        app_version="scenario",
        protocol_version="1",
        platform="scenario",
        connectable=True,
        compatible=True,
        last_seen=0.0,
        identity_fingerprint=raw.get("identity_fingerprint"),
        transport_fingerprint=raw.get("transport_fingerprint"),
        endpoint_candidates=(endpoint,),
        root_public_key=raw.get("root_public_key"),
        transport_generation=raw.get("transport_generation"),
        transport_proof=raw.get("transport_proof"),
    )


class NodeController:
    def __init__(self, profile: Path, name: str) -> None:
        from expra_connect import ConnectConfig, ConnectRuntime

        self.name = name

        def approve_pairing(request: Any) -> bool:
            return bool(request.permissions)

        self.runtime = ConnectRuntime(
            ConnectConfig(
                profile_dir=profile,
                display_name=name,
                bind_host="127.0.0.1",
                preferred_port=0,
                discovery_enabled=False,
                advertised_addresses=("127.0.0.1",),
                on_pairing_request=approve_pairing,
            )
        )

    def _advertisement(self) -> dict[str, Any]:
        identity = self.runtime.identity
        generations = self.runtime.transport_generations
        status = self.runtime.status
        if identity is None or generations is None:
            raise RuntimeError("node identity is unavailable")
        generation = generations.current
        from expra_connect.identity import node_identity_fingerprint

        return {
            "node_id": identity.node_id.value,
            "host": status.bound_host or "127.0.0.1",
            "port": status.bound_port,
            "identity_fingerprint": node_identity_fingerprint(identity.node_id),
            "transport_fingerprint": status.tls_fingerprint,
            "root_public_key": identity.root_public_key,
            "transport_generation": generation.generation,
            "transport_proof": generation.proof,
        }

    def _state(self) -> dict[str, Any]:
        pairing = self.runtime.pairing
        return {
            "name": self.name,
            "node_id": self.runtime.identity.node_id.value
            if self.runtime.identity is not None
            else None,
            "state": self.runtime.status.state.value,
            "bound_port": self.runtime.status.bound_port,
            "connection_count": len(self.runtime.connections),
            "trusted_peer_count": len(pairing.trusted) if pairing is not None else 0,
        }

    def _resources(self) -> dict[str, int]:
        socket_count = 0
        fd_root = "/proc/self/fd"
        if os.path.isdir(fd_root):
            for descriptor in os.listdir(fd_root):
                try:
                    if os.readlink(os.path.join(fd_root, descriptor)).startswith(
                        "socket:"
                    ):
                        socket_count += 1
                except OSError:
                    pass
        return {
            "registry_count": (
                len(self.runtime._registry.records)
                if self.runtime._registry is not None
                else 0
            ),
            "thread_count": len(threading.enumerate()),
            "task_count": 0,
            "socket_count": socket_count,
        }

    def handle(self, command: str, payload: dict[str, Any]) -> dict[str, Any]:
        from expra_connect import NodeId

        if command == "start":
            status = self.runtime.start()
            result: dict[str, Any] = {"state": self._state()}
            if status.state.value == "started":
                result["advertisement"] = self._advertisement()
            return result
        if command == "shutdown":
            self.runtime.shutdown()
            return {"state": self._state()}
        if command == "restart":
            self.runtime.shutdown()
            status = self.runtime.start()
            result = {"state": self._state()}
            if status.state.value == "started":
                result["advertisement"] = self._advertisement()
            return result
        if command == "rotate":
            status = self.runtime.rotate_transport()
            return {"state": self._state(), "advertisement": self._advertisement()}
        if command == "inject_candidate":
            candidate = _candidate(payload)
            self.runtime._on_discovery(self.runtime._generation, "candidate", candidate)
            return {"state": self._state()}
        if command == "force_candidate":
            candidate = _candidate(payload)
            self.runtime._peers[candidate.stable_id] = candidate
            return {"state": self._state()}
        if command == "pair":
            self.runtime.pair_peer(NodeId(payload["peer_id"]))
            return {"state": self._state()}
        if command == "connect":
            self.runtime.connect_peer(NodeId(payload["peer_id"]))
            return {"state": self._state()}
        if command == "disconnect":
            self.runtime.disconnect_peer(NodeId(payload["peer_id"]), reason="scenario")
            return {"state": self._state()}
        if command == "reconnect":
            self.runtime.reconnect_peer(NodeId(payload["peer_id"]))
            return {"state": self._state()}
        if command == "revoke":
            self.runtime.revoke_peer(NodeId(payload["peer_id"]))
            return {"state": self._state()}
        if command == "capability_probe":
            manager = self.runtime._connection_manager
            if manager is None:
                raise RuntimeError("runtime has not started")
            provider = manager._providers[NodeId(payload["peer_id"])]
            hello = provider.hello()
            try:
                provider.request_shared(payload["capability"])
            except Exception as error:  # noqa: BLE001 - return type only.
                return {
                    "authorized": False,
                    "advertised_capabilities": hello.get("capabilities", []),
                    "error_type": type(error).__name__,
                }
            return {
                "authorized": True,
                "advertised_capabilities": hello.get("capabilities", []),
            }
        if command == "stale_transaction":
            pairing = self.runtime.pairing
            if pairing is None:
                raise RuntimeError("runtime has not started")
            pending = pairing.begin(NodeId(payload["peer_id"]))
            try:
                pairing.confirm(
                    pending.transaction_id,
                    now=pending.expires_at + 1.0,
                )
            except ValueError:
                return {"rejected": True}
            return {"rejected": False}
        if command == "state":
            return {"state": self._state()}
        if command == "advertisement":
            return {"advertisement": self._advertisement()}
        if command == "resources":
            return {"resources": self._resources()}
        raise ValueError(f"unsupported node command: {command}")


def main() -> int:
    profile = Path(sys.argv[1])
    controller = NodeController(profile, sys.argv[2])
    for line in sys.stdin:
        if not line.strip():
            continue
        request = json.loads(line)
        try:
            value = controller.handle(request["command"], request.get("payload", {}))
            response = {"ok": True, **value}
        except Exception as error:  # noqa: BLE001 - child boundary is typed below.
            response = {"ok": False, "error_type": type(error).__name__}
        print(json.dumps(response, sort_keys=True), flush=True)
    controller.runtime.shutdown()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""One-shot loopback evidence worker run under configured Expra Python."""

from __future__ import annotations

import hashlib
import hmac
import json
import socket
import ssl
import sys
import tempfile
from pathlib import Path
from typing import Any


def _fingerprint(certificate: bytes) -> str:
    digest = hashlib.sha256(certificate).hexdigest()
    return ":".join(digest[index : index + 4] for index in range(0, 64, 4))


def _stage(stage: str, outcome: str, detail: str | None = None) -> dict[str, str]:
    value = {"stage": stage, "outcome": outcome}
    if detail is not None:
        value["detail"] = detail
    return value


def _base(probe: str, action: str, mode: str) -> dict[str, Any]:
    return {
        "probe": probe,
        "action": action,
        "status": "PASS",
        "execution_mode": mode,
        "stages": [],
        "evidence": {},
        "reasons": [],
    }


def _runtime_config(profile: Path, *, discovery: bool, factory: Any = None) -> Any:
    from expra_connect import ConnectConfig

    return ConnectConfig(
        profile_dir=profile,
        bind_host="127.0.0.1",
        preferred_port=0,
        discovery_enabled=discovery,
        advertised_addresses=("127.0.0.1",),
        discovery_backend_factory=factory,
    )


def _listen_state() -> dict[str, Any]:
    from expra_connect import ConnectRuntime

    result = _base("transport", "listen_state", "LOOPBACK_EXECUTION")
    with tempfile.TemporaryDirectory(prefix="expra-mcp-transport-") as directory:
        runtime = ConnectRuntime(_runtime_config(Path(directory), discovery=False))
        try:
            status = runtime.start()
            result["evidence"] = {
                "state": status.state.value,
                "bound_host": status.bound_host,
                "bound_port": status.bound_port,
                "tls_fingerprint": status.tls_fingerprint,
                "preferred_port_honored": status.preferred_port_honored,
            }
            result["stages"].extend(
                [
                    _stage(
                        "LISTENER_BIND",
                        "PASS" if status.listener_started else "FAIL",
                        status.reason,
                    ),
                    _stage(
                        "TLS_CONFIGURATION",
                        "PASS" if status.tls_fingerprint else "FAIL",
                    ),
                    _stage(
                        "ADVERTISEMENT_ENDPOINT",
                        "PASS" if status.bound_port else "FAIL",
                        "actual listener port is available",
                    ),
                ]
            )
            if not status.listener_started or not status.tls_fingerprint:
                result["status"] = "FAIL"
        finally:
            runtime.shutdown()
    return result


def _local_discovery() -> dict[str, Any]:
    from expra_connect import ConnectRuntime
    from expra_connect.discovery_full import SERVICE_TYPE

    result = _base("discovery", "local_loopback", "LOOPBACK_EXECUTION")
    raw_events: list[str] = []
    backend_holder: dict[str, Any] = {}

    class Info:
        properties: dict[bytes, bytes]
        port: int

        def __init__(self, node_id: str, port: int) -> None:
            self.addresses = ["127.0.0.1"]
            self.port = port
            self.properties = {
                b"id": node_id.encode(),
                b"name": node_id.encode(),
                b"app_version": b"probe",
                b"protocol_version": b"1",
                b"connectable": b"true",
                b"tls_fingerprint": b"probe-fingerprint",
            }

    class Backend:
        available = True

        def __init__(self, listener: Any) -> None:
            self.listener = listener
            self.advertisement: Any = None

        def start(self, advertisement: Any) -> None:
            self.advertisement = advertisement

        def emit(self) -> None:
            advertisement = self.advertisement
            raw_events.append("peer")
            self.listener(
                "add",
                f"peer-loopback.{SERVICE_TYPE}",
                Info("peer-loopback", advertisement.port),
            )
            self.listener(
                "add",
                f"{advertisement.stable_id}.{SERVICE_TYPE}",
                Info(advertisement.stable_id, advertisement.port),
            )
            self.listener(
                "add",
                f"{advertisement.stable_id}.{SERVICE_TYPE}",
                Info(advertisement.stable_id, advertisement.port),
            )

        def stop(self) -> None:
            pass

    def factory(listener: Any) -> Backend:
        backend = Backend(listener)
        backend_holder["backend"] = backend
        return backend

    with tempfile.TemporaryDirectory(prefix="expra-mcp-discovery-") as directory:
        runtime = ConnectRuntime(
            _runtime_config(
                Path(directory),
                discovery=True,
                factory=factory,
            )
        )
        try:
            status = runtime.start()
            backend_holder["backend"].emit()
            peers = [
                {
                    "node_id": peer.stable_id,
                    "addresses": peer.addresses,
                    "port": peer.port,
                    "connectable": peer.connectable,
                    "compatible": peer.compatible,
                    "endpoint_count": len(peer.endpoint_candidates),
                }
                for peer in runtime.peers
            ]
            component_peers = (
                [peer.stable_id for peer in runtime._discovery.peers()]
                if runtime._discovery is not None
                else []
            )
            identity = runtime.identity
            identity_node_id = (
                identity.node_id.value
                if identity is not None and identity.node_id is not None
                else None
            )
            result["evidence"] = {
                "listener_port": status.bound_port,
                "discovery_started": status.discovery_started,
                "peers": peers,
                "component_peers": component_peers,
                "raw_events": raw_events,
                "self_filtered": all(
                    peer["node_id"] != identity_node_id for peer in peers
                ),
            }
            result["stages"].extend(
                [
                    _stage("LISTENER_BIND", "PASS" if status.bound_port else "FAIL"),
                    _stage(
                        "DISCOVERY_START",
                        "PASS" if status.discovery_started else "FAIL",
                        status.discovery_reason,
                    ),
                    _stage(
                        "CANDIDATE_NORMALIZATION",
                        "PASS" if peers else "FAIL",
                    ),
                    _stage(
                        "SELF_DISCOVERY_FILTER",
                        "PASS" if result["evidence"]["self_filtered"] else "FAIL",
                    ),
                ]
            )
            if not status.discovery_started or not peers:
                result["status"] = "FAIL"
        finally:
            runtime.shutdown()
    return result


def _live_discovery() -> dict[str, Any]:
    from expra_connect import ConnectRuntime

    result = _base("discovery", "live_discovery", "LAN_EXECUTION")
    with tempfile.TemporaryDirectory(prefix="expra-mcp-live-discovery-") as directory:
        runtime = ConnectRuntime(_runtime_config(Path(directory), discovery=True))
        try:
            status = runtime.start()
            result["evidence"] = {
                "listener_port": status.bound_port,
                "discovery_started": status.discovery_started,
                "discovery_reason": status.discovery_reason,
                "peers": [peer.stable_id for peer in runtime.peers],
            }
            result["stages"] = [
                _stage("LISTENER_BIND", "PASS" if status.bound_port else "FAIL"),
                _stage(
                    "LIVE_DISCOVERY_START",
                    "PASS" if status.discovery_started else "FAIL",
                    status.discovery_reason,
                ),
                _stage("CANDIDATE_OBSERVATION", "PASS"),
            ]
            if not status.discovery_started:
                result["status"] = "FAIL"
        finally:
            runtime.shutdown()
    return result


def _connect_loopback(host: str, port: int, expected: str | None) -> dict[str, Any]:
    result = _base("transport", "connect_loopback", "LOOPBACK_EXECUTION")
    if not 1 <= port <= 65535:
        result["status"] = "FAIL"
        result["stages"] = [
            _stage("TRANSPORT_ENDPOINT_INVALID", "FAIL", f"port={port}")
        ]
        result["evidence"] = {"host": host, "port": port}
        return result
    result["evidence"] = {"host": host, "port": port}
    try:
        with socket.create_connection((host, port), timeout=5.0) as raw:
            result["stages"].append(_stage("TCP_CONNECT", "PASS"))
            context = ssl.create_default_context()
            context.check_hostname = False
            context.verify_mode = ssl.CERT_NONE
            with context.wrap_socket(raw, server_hostname=host) as secure:
                result["stages"].append(_stage("TLS_HANDSHAKE", "PASS"))
                certificate = secure.getpeercert(binary_form=True)
                observed = _fingerprint(certificate) if certificate else None
                result["evidence"]["fingerprint_observed"] = observed
                if expected is not None and observed is not None:
                    matched = hmac.compare_digest(observed, expected)
                    result["stages"].append(
                        _stage(
                            "FINGERPRINT_VERIFY",
                            "PASS" if matched else "FAIL",
                        )
                    )
                    if not matched:
                        result["status"] = "FAIL"
                else:
                    result["stages"].append(_stage("FINGERPRINT_VERIFY", "NOT_RUN"))
                result["stages"].append(
                    _stage(
                        "AUTHENTICATION",
                        "NOT_RUN",
                        "application authentication requires pairing material",
                    )
                )
    except ssl.SSLError as error:
        result["status"] = "FAIL"
        result["stages"].append(_stage("TLS_HANDSHAKE", "FAIL", type(error).__name__))
    except OSError as error:
        result["status"] = "FAIL"
        result["stages"].append(_stage("TCP_CONNECT", "FAIL", type(error).__name__))
    return result


def _loopback_handshake() -> dict[str, Any]:
    from expra_connect import ConnectRuntime

    with tempfile.TemporaryDirectory(prefix="expra-mcp-handshake-") as directory:
        runtime = ConnectRuntime(_runtime_config(Path(directory), discovery=False))
        try:
            status = runtime.start()
            if status.bound_port is None:
                return {
                    "probe": "transport",
                    "action": "loopback_handshake",
                    "status": "FAIL",
                    "execution_mode": "LOOPBACK_EXECUTION",
                    "stages": [_stage("LISTENER_BIND", "FAIL")],
                    "evidence": {},
                    "reasons": ["runtime did not expose a listener port"],
                }
            result = _connect_loopback(
                "127.0.0.1", status.bound_port, status.tls_fingerprint
            )
            result["action"] = "loopback_handshake"
            result["evidence"]["listener_port"] = status.bound_port
            return result
        finally:
            runtime.shutdown()


def _connection_state() -> dict[str, Any]:
    from expra_connect import ConnectRuntime

    result = _base("connection", "state", "LOOPBACK_EXECUTION")
    with tempfile.TemporaryDirectory(prefix="expra-mcp-connection-") as directory:
        runtime = ConnectRuntime(_runtime_config(Path(directory), discovery=False))
        try:
            status = runtime.start()
            diagnostics = runtime.diagnostics()
            result["evidence"] = {
                "runtime_state": status.state.value,
                "bound_port": status.bound_port,
                "connections": diagnostics.get("connections", []),
                "sessions": diagnostics.get("sessions", []),
                "retry_state": "NOT_PRESENT: no peer connection exists",
            }
            result["stages"] = [
                _stage("RUNTIME_STATE", "PASS" if runtime.started else "FAIL"),
                _stage("CONNECTION_STATE", "PASS"),
                _stage("RETRY_STATE", "NOT_RUN", "no peer was selected"),
            ]
            if not runtime.started:
                result["status"] = "FAIL"
        finally:
            runtime.shutdown()
    return result


def main() -> int:
    probe = sys.argv[1]
    action = sys.argv[2]
    host = sys.argv[3] if len(sys.argv) > 3 else "127.0.0.1"
    port = int(sys.argv[4]) if len(sys.argv) > 4 else 0
    expected = sys.argv[5] if len(sys.argv) > 5 and sys.argv[5] else None
    if probe == "discovery":
        value = _live_discovery() if action == "live_discovery" else _local_discovery()
    elif probe == "transport":
        if action == "connect_loopback":
            value = _connect_loopback(host, port, expected)
        elif action == "loopback_handshake":
            value = _loopback_handshake()
        else:
            value = _listen_state()
    elif probe == "connection":
        value = _connection_state()
    else:
        value = {"status": "NOT_PRESENT", "reasons": ["unknown probe"]}
    print(json.dumps(value, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

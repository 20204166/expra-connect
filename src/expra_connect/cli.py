"""Headless diagnostics and operation CLI backed by :class:`ConnectRuntime`."""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from dataclasses import asdict
from enum import Enum
from pathlib import Path
from typing import Any

from ._version import __version__
from .identity import NodeId, node_identity_fingerprint
from .models import READ_CAPABILITIES
from .remote_models import NodeStatus
from .remote_service import AuthenticatedNodeProvider, RemoteService
from .runtime import ConnectConfig, ConnectRuntime
from .server import RemoteSocketServer
from .sharing import CapabilityShare
from .socket_transport import SocketRemoteTransport


def _default_profile() -> Path:
    root = os.environ.get("XDG_STATE_HOME")
    if sys.platform == "win32":
        windows_root = (
            os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local"
        )
        return Path(windows_root).expanduser() / "expra-connect"
    return (
        Path(root).expanduser() / "expra-connect"
        if root
        else Path.home() / ".local" / "state" / "expra-connect"
    )


def _runtime(args: argparse.Namespace) -> ConnectRuntime:
    return ConnectRuntime(
        ConnectConfig(
            profile_dir=args.profile,
            discovery_enabled=not args.no_discovery,
            cluster_enabled=args.cluster,
        )
    )


def _json_default(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, Path):
        return str(value)
    return str(value)


def _print(value: Any, *, human: bool = False, command: str = "") -> None:
    if human:
        print(format_human_output(command, value))
        return
    print(json.dumps(value, indent=2, sort_keys=True, default=_json_default))


def _status(runtime: ConnectRuntime) -> dict[str, Any]:
    return asdict(runtime.status)


def _identity(runtime: ConnectRuntime) -> dict[str, str] | None:
    identity = runtime.identity
    if identity is None:
        return None
    return {
        "node_id": identity.node_id.value,
        "identity_fingerprint": node_identity_fingerprint(identity.node_id),
    }


def _diagnostics(runtime: ConnectRuntime) -> dict[str, Any]:
    result: dict[str, Any] = {"version": __version__, **runtime.diagnostics()}
    result["peers"] = [asdict(peer) for peer in runtime.peers]
    if runtime.pairing is not None:
        result["trust"] = {
            "trusted_peers": sorted(peer.value for peer in runtime.pairing.trusted),
            "granted_peers": sorted(peer.value for peer in runtime.pairing.grants),
            "pending_pairings": sorted(runtime.pairing.pending),
        }
    if runtime.cluster is not None:
        cluster = runtime.cluster
        result["cluster"] = {
            "cluster_id": cluster.cluster_id,
            "local_id": cluster.local_id.value,
            "coordinator_id": cluster.coordinator_id.value,
            "epoch": cluster.epoch.epoch,
            "role": cluster.local_role.value,
            "members": sorted(node.value for node in cluster.assignments),
        }
    return result


def _human_value(value: Any) -> str:
    if isinstance(value, bool):
        return str(value).lower()
    if value is None:
        return "-"
    if isinstance(value, Enum):
        return str(value.value)
    return str(value)


def _human_item(value: Any) -> str:
    if isinstance(value, (list, tuple)):
        return ",".join(_human_value(item) for item in value)
    return _human_value(value)


def _human_status(status: dict[str, Any]) -> list[str]:
    return [
        f"state={_human_value(status.get('state'))}",
        f"listener={'started' if status.get('listener_started') else 'not_started'}",
        f"discovery={'started' if status.get('discovery_started') else 'not_started'}",
        (
            f"bound={status.get('bound_host', '-') or '-'}:"
            f"{status.get('bound_port', '-') or '-'}"
        ),
        f"tls_fingerprint={_human_value(status.get('tls_fingerprint'))}",
    ]


def _human_peer(peer: dict[str, Any], number: int) -> list[str]:
    lines = [
        (
            f"peer[{number}] id={_human_value(peer.get('stable_id'))} "
            f"host={_human_value(peer.get('hostname'))}"
        )
    ]
    endpoints = peer.get("endpoint_candidates") or []
    for endpoint in endpoints:
        if not isinstance(endpoint, dict):
            continue
        source = endpoint.get("source")
        lines.append(
            f"  endpoint={_human_value(endpoint.get('address'))}:"
            f"{_human_value(endpoint.get('port'))} source={_human_value(source)}"
        )
    return lines


def format_human_output(command: str, value: Any) -> str:
    """Render CLI values in stable order without printing secret-bearing fields."""

    if command == "status" and isinstance(value, dict):
        return "\n".join(_human_status(value))
    if command == "identity" and isinstance(value, dict):
        return "\n".join(
            f"{key}={_human_value(value.get(key))}"
            for key in ("node_id", "identity_fingerprint")
        )
    if command == "peers" and isinstance(value, list):
        if not value:
            return "peers=0"
        return "\n".join(
            line
            for number, peer in enumerate(value, 1)
            for line in _human_peer(peer, number)
        )
    if command == "diagnostics" and isinstance(value, dict):
        lines = [f"version={_human_value(value.get('version'))}", "status:"]
        lines.extend(f"  {line}" for line in _human_status(value.get("status", {})))
        for section in ("identity", "transport", "trust", "cluster"):
            data = value.get(section)
            if not data:
                continue
            lines.append(f"{section}:")
            for key, item in data.items():
                if key in {"root_public_key", "transport_proof", "secret", "token"}:
                    continue
                display = len(item) if isinstance(item, (list, tuple, dict)) else item
                lines.append(f"  {key}={_human_value(display)}")
        lines.append("peers:")
        peers = value.get("peers") or []
        lines.extend(
            f"  {line}"
            for number, peer in enumerate(peers, 1)
            for line in _human_peer(peer, number)
        )
        lines.append("observability:")
        metrics = value.get("observability", {}).get("metrics", [])
        if not metrics:
            lines.append("  metrics=0")
        for metric in metrics:
            lines.append(
                f"  metric target={metric.get('target', '-')} count={metric.get('count', 0)} "
                f"success={metric.get('successes', 0)} failure={metric.get('failures', 0)}"
            )
        return "\n".join(lines)
    if isinstance(value, dict):
        return "\n".join(
            f"{key}={_human_item(item)}"
            for key, item in value.items()
            if key
            not in {"secret", "token", "root_public_key", "transport_proof", "result"}
        )
    return _human_value(value)


def _run_demo(args: argparse.Namespace) -> int:
    if args.action == "ping":
        _print({"ok": True, "operation": "ping"})
        return 0
    if args.action == "loopback":
        secret = "a" * 64

        class Provider:
            pass

        service = RemoteService(
            node_id=NodeId("demo-peer"),
            display_name="Demo Peer",
            hostname="localhost",
            platform=None,
            status=NodeStatus.ONLINE,
            capabilities=READ_CAPABILITIES,
            provider=Provider(),
            secret=secret,
        )
        server = RemoteSocketServer(service)
        server.start()
        try:
            client = AuthenticatedNodeProvider(
                node_id=NodeId("demo-peer"),
                secret=secret,
                transport=SocketRemoteTransport("127.0.0.1", server.bound_port or 0),
            )
            _print(client.hello())
        finally:
            server.stop()
        return 0
    share = CapabilityShare()
    share.register("demo.read_state", lambda _peer, _params: {"state": "ready"})
    share.allow(NodeId("demo-peer"), "demo.read_state")
    _print(share.request(NodeId("demo-peer"), "demo.read_state"))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="expra-peer")
    parser.add_argument("--version", action="version", version=__version__)
    parser.add_argument("--profile", type=Path, default=_default_profile())
    parser.add_argument("--no-discovery", action="store_true")
    parser.add_argument("--cluster", action="store_true")
    parser.add_argument(
        "--human", action="store_true", help="print ordered audit output"
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("status")
    subparsers.add_parser("identity")
    subparsers.add_parser("peers")
    subparsers.add_parser("diagnostics")
    subparsers.add_parser("serve")
    pair = subparsers.add_parser("pair")
    pair.add_argument("peer_id")
    revoke = subparsers.add_parser("revoke")
    revoke.add_argument("peer_id")
    demo = subparsers.add_parser("demo")
    demo.add_argument("action", choices=("ping", "share", "loopback"))
    args = parser.parse_args(argv)
    if args.command == "demo":
        return _run_demo(args)

    runtime = _runtime(args)
    status = runtime.start()
    try:
        if args.command == "status":
            _print(_status(runtime), human=args.human, command=args.command)
        elif args.command == "identity":
            _print(_identity(runtime), human=args.human, command=args.command)
        elif args.command == "peers":
            _print(
                [asdict(peer) for peer in runtime.peers],
                human=args.human,
                command=args.command,
            )
        elif args.command == "diagnostics":
            _print(_diagnostics(runtime), human=args.human, command=args.command)
        elif args.command == "revoke":
            runtime.revoke_peer(NodeId(args.peer_id))
            _print(
                {"ok": True, "revoked": args.peer_id},
                human=args.human,
                command=args.command,
            )
        elif args.command == "pair":
            trusted = runtime.pair_peer(NodeId(args.peer_id))
            _print(
                {
                    "ok": True,
                    "peer_id": trusted.peer_id.value,
                    "permissions": sorted(trusted.permissions),
                },
                human=args.human,
                command=args.command,
            )
        elif args.command == "serve":
            _print(_status(runtime), human=args.human, command=args.command)
            if status.state.value != "started":
                return 1
            while True:
                time.sleep(1)
    except KeyboardInterrupt:
        return 0
    finally:
        runtime.shutdown()
    return 0 if status.state.value != "persistence_failed" else 1


if __name__ == "__main__":
    raise SystemExit(main())

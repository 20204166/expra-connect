"""Small headless demonstration CLI."""

from __future__ import annotations

import argparse
import json

from .identity import NodeId
from .models import READ_CAPABILITIES
from .remote_models import NodeStatus
from .remote_service import AuthenticatedNodeProvider, RemoteService
from .server import RemoteSocketServer
from .sharing import CapabilityShare
from .socket_transport import SocketRemoteTransport


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="expra-peer")
    subparsers = parser.add_subparsers(dest="command", required=True)
    demo = subparsers.add_parser("demo")
    demo.add_argument("action", choices=("ping", "share", "loopback"))
    args = parser.parse_args(argv)
    if args.command == "demo" and args.action == "ping":
        print(json.dumps({"ok": True, "operation": "ping"}))
        return 0
    if args.command == "demo" and args.action == "loopback":
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
            print(json.dumps(client.hello()))
        finally:
            server.stop()
        return 0
    share = CapabilityShare()
    share.register("demo.read_state", lambda _peer, _params: {"state": "ready"})
    share.allow(NodeId("demo-peer"), "demo.read_state")
    print(json.dumps(share.request(NodeId("demo-peer"), "demo.read_state")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

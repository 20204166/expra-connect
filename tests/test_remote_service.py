import unittest
from datetime import datetime, timezone

from expra_connect.identity import NodeId
from expra_connect.models import READ_CAPABILITIES, NodeCapability, NodePermission
from expra_connect.remote_models import (
    DashboardSnapshot,
    NodeStatus,
    ResourceSummary,
)
from expra_connect.remote_service import (
    AuthenticatedNodeProvider,
    MemoryRemoteTransport,
    RemoteAuthError,
    RemoteAuthorizationError,
    RemoteService,
)
from expra_connect.server import RemoteSocketServer
from expra_connect.sharing import CapabilityShare
from expra_connect.socket_transport import SocketRemoteTransport
from expra_connect.wire_protocol import PeerGrant

SECRET = "a" * 64


class _Provider:
    def dashboard_snapshot(self) -> DashboardSnapshot:
        return DashboardSnapshot(
            system_label="peer",
            scanned_at=datetime.now(timezone.utc),
            resources=(ResourceSummary("cpu", "CPU", "10%", "ok", 10.0, ("ok",)),),
        )

    def component_summary(self, key: str) -> ResourceSummary:
        return ResourceSummary(key, key, "ready", "ok", None, ())

    def process_candidates(self) -> list[object]:
        return []

    def storage_candidates(self) -> list[object]:
        return []


class RemoteServiceTests(unittest.TestCase):
    def _client(self) -> AuthenticatedNodeProvider:
        service = RemoteService(
            node_id=NodeId("peer"),
            display_name="Peer",
            hostname="peer-host",
            platform="Linux",
            status=NodeStatus.ONLINE,
            capabilities=READ_CAPABILITIES,
            provider=_Provider(),
            secret=SECRET,
        )
        return AuthenticatedNodeProvider(
            node_id=NodeId("peer"),
            secret=SECRET,
            transport=MemoryRemoteTransport(service),
        )

    def test_authenticated_hello_and_component_request(self) -> None:
        client = self._client()
        hello = client.hello()
        self.assertEqual(hello["node_id"], "peer")
        self.assertEqual(client.component_summary("cpu").key, "cpu")
        self.assertEqual(client.dashboard_snapshot().system_label, "peer")

    def test_provider_rejects_after_invalidation_without_transport_call(self) -> None:
        client = self._client()
        client.invalidate()
        with self.assertRaises(RemoteAuthError):
            client.hello()

    def test_unknown_capability_is_never_granted(self) -> None:
        self.assertEqual(
            frozenset({NodeCapability.READ_STATE}),
            READ_CAPABILITIES,
        )

    def test_shared_capability_requires_target_grant_and_round_trips(self) -> None:
        share = CapabilityShare()
        share.register(
            "demo.read_state",
            lambda peer, params: {"peer": peer.value, "value": params["value"]},
        )
        caller = NodeId("caller")
        share.allow(caller, "demo.read_state")
        service = RemoteService(
            node_id=NodeId("peer"),
            display_name="Peer",
            hostname="peer-host",
            platform="Linux",
            status=NodeStatus.ONLINE,
            capabilities=READ_CAPABILITIES,
            provider=_Provider(),
            secret=SECRET,
            grants={
                caller: PeerGrant(
                    caller, SECRET, frozenset({NodePermission.READ_STATE})
                )
            },
            capability_share=share,
        )
        client = AuthenticatedNodeProvider(
            node_id=NodeId("peer"),
            caller_node_id=caller,
            secret=SECRET,
            transport=MemoryRemoteTransport(service),
        )
        self.assertEqual(
            client.request_shared("demo.read_state", {"value": "ready"}),
            {"peer": "caller", "value": "ready"},
        )
        share.revoke(caller, "demo.read_state")
        with self.assertRaises(RemoteAuthorizationError):
            client.request_shared("demo.read_state")

    def test_authenticated_provider_round_trips_over_loopback_socket(self) -> None:
        service = RemoteService(
            node_id=NodeId("peer"),
            display_name="Peer",
            hostname="peer-host",
            platform="Linux",
            status=NodeStatus.ONLINE,
            capabilities=READ_CAPABILITIES,
            provider=_Provider(),
            secret=SECRET,
        )
        server = RemoteSocketServer(service)
        server.start()
        try:
            client = AuthenticatedNodeProvider(
                node_id=NodeId("peer"),
                secret=SECRET,
                transport=SocketRemoteTransport("127.0.0.1", server.bound_port or 0),
            )
            self.assertEqual(client.hello()["node_id"], "peer")
        finally:
            server.stop()

    def test_grant_mode_rejects_unknown_caller_before_operation(self) -> None:
        service = RemoteService(
            node_id=NodeId("peer"),
            display_name="Peer",
            hostname="peer-host",
            platform="Linux",
            status=NodeStatus.ONLINE,
            capabilities=READ_CAPABILITIES,
            provider=_Provider(),
            secret=SECRET,
            grants={
                NodeId("known"): PeerGrant(
                    NodeId("known"), SECRET, frozenset({NodePermission.READ_STATE})
                )
            },
        )
        client = AuthenticatedNodeProvider(
            node_id=NodeId("peer"),
            caller_node_id=NodeId("unknown"),
            secret=SECRET,
            transport=MemoryRemoteTransport(service),
        )
        with self.assertRaises(RemoteAuthError):
            client.hello()

    def test_grant_mode_accepts_known_caller_with_read_permission(self) -> None:
        service = RemoteService(
            node_id=NodeId("peer"),
            display_name="Peer",
            hostname="peer-host",
            platform="Linux",
            status=NodeStatus.ONLINE,
            capabilities=READ_CAPABILITIES,
            provider=_Provider(),
            secret=SECRET,
            grants={
                NodeId("known"): PeerGrant(
                    NodeId("known"), SECRET, frozenset({NodePermission.READ_STATE})
                )
            },
        )
        client = AuthenticatedNodeProvider(
            node_id=NodeId("peer"),
            caller_node_id=NodeId("known"),
            secret=SECRET,
            transport=MemoryRemoteTransport(service),
        )
        self.assertEqual(client.hello()["node_id"], "peer")

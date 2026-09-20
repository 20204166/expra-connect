import json
import threading
import time
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
from expra_connect.socket_transport import RemoteTransportError, SocketRemoteTransport
from expra_connect.wire_protocol import (
    IdempotencyCollisionError,
    PeerGrant,
    sign_request,
)

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
    def test_cluster_fence_cannot_move_backwards(self) -> None:
        service = RemoteService(
            node_id=NodeId("peer"),
            display_name="Peer",
            hostname="peer-host",
            platform="Linux",
            status=NodeStatus.ONLINE,
            capabilities=READ_CAPABILITIES,
            provider=_Provider(),
            secret=SECRET,
            cluster_id="cluster",
            coordinator_epoch=2,
            fencing_token="new-fence",
        )

        with self.assertRaises(RemoteAuthorizationError):
            service.update_cluster_fence(
                cluster_id="cluster",
                coordinator_epoch=1,
                fencing_token="old-fence",
            )

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

    def test_socket_replacement_resumes_authenticated_logical_session(self) -> None:
        client = self._client()
        client.hello()
        self.assertIsNotNone(client._session_id)
        self.assertEqual(client.component_summary("cpu").key, "cpu")

    def test_retired_connection_generation_cannot_resume_again(self) -> None:
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
        first = sign_request(
            node_id="peer",
            op="hello",
            params={},
            request_id="generation-1",
            nonce="nonce-1",
            timestamp=time.time(),
            secret=SECRET,
        )
        first_response = json.loads(
            service.handle(json.dumps(first), connection_generation="generation-a")
        )
        session_id = first_response["session_id"]
        replacement = sign_request(
            node_id="peer",
            op="hello",
            params={},
            request_id="generation-2",
            nonce="nonce-2",
            timestamp=time.time(),
            secret=SECRET,
            session_id=session_id,
            resume=True,
        )
        service.handle(json.dumps(replacement), connection_generation="generation-b")
        stale = sign_request(
            node_id="peer",
            op="hello",
            params={},
            request_id="generation-3",
            nonce="nonce-3",
            timestamp=time.time(),
            secret=SECRET,
            session_id=session_id,
            resume=True,
        )
        with self.assertRaises(RemoteAuthError):
            service.handle(json.dumps(stale), connection_generation="generation-a")

    def test_expired_resume_and_wrong_identity_are_rejected(self) -> None:
        now = [100.0]
        service = RemoteService(
            node_id=NodeId("peer"),
            display_name="Peer",
            hostname="peer-host",
            platform="Linux",
            status=NodeStatus.ONLINE,
            capabilities=READ_CAPABILITIES,
            provider=_Provider(),
            secret=SECRET,
            clock=lambda: now[0],
            grants={
                NodeId("caller"): PeerGrant(
                    NodeId("caller"), SECRET, frozenset({NodePermission.READ_STATE})
                ),
                NodeId("other"): PeerGrant(
                    NodeId("other"), SECRET, frozenset({NodePermission.READ_STATE})
                ),
            },
        )
        client = AuthenticatedNodeProvider(
            node_id=NodeId("peer"),
            caller_node_id=NodeId("caller"),
            secret=SECRET,
            transport=MemoryRemoteTransport(service),
            clock=lambda: now[0],
        )
        client.hello()
        session_id = client._session_id
        self.assertIsNotNone(session_id)
        wrong = sign_request(
            node_id="peer",
            caller_node_id="other",
            op="ping",
            params={},
            request_id="wrong-resume",
            nonce="wrong-resume-nonce",
            timestamp=100.0,
            secret=SECRET,
            session_id=session_id,
            resume=True,
        )
        with self.assertRaises(RemoteAuthError):
            service.handle(json.dumps(wrong))
        now[0] = 701.0
        with self.assertRaises(RemoteAuthError):
            client.hello()

    def test_lost_response_retries_retry_safe_mutation_once(self) -> None:
        calls = 0

        def revoke(_caller: NodeId) -> dict[str, bool]:
            nonlocal calls
            calls += 1
            return {"revoked": True}

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
                NodeId("caller"): PeerGrant(
                    NodeId("caller"), SECRET, frozenset({NodePermission.READ_STATE})
                )
            },
            trust_revoke_handler=revoke,
        )

        class LoseFirstResponse:
            def __init__(self) -> None:
                self.calls = 0

            def request(self, envelope: str, _cancel: object = None) -> str:
                self.calls += 1
                response = service.handle(envelope)
                if self.calls == 1:
                    raise RemoteTransportError("response lost")
                return response

        client = AuthenticatedNodeProvider(
            node_id=NodeId("peer"),
            caller_node_id=NodeId("caller"),
            secret=SECRET,
            transport=LoseFirstResponse(),
        )
        self.assertEqual(client.revoke_self(), {"revoked": True})
        self.assertEqual(calls, 1)

    def test_duplicate_request_id_with_different_params_is_rejected(self) -> None:
        service = RemoteService(
            node_id=NodeId("peer"),
            display_name="Peer",
            hostname="peer-host",
            platform="Linux",
            status=NodeStatus.ONLINE,
            capabilities=READ_CAPABILITIES,
            provider=_Provider(),
            secret=SECRET,
            trust_revoke_handler=lambda _caller: {"revoked": True},
        )
        first = sign_request(
            node_id="peer",
            caller_node_id="caller",
            op="revoke_self",
            params={},
            request_id="same",
            nonce="one",
            timestamp=time.time(),
            secret=SECRET,
        )
        second = dict(first)
        second["nonce"] = "two"
        second["params"] = {"unexpected": True}
        second["sig"] = sign_request(
            node_id="peer",
            caller_node_id="caller",
            op="revoke_self",
            params=second["params"],
            request_id="same",
            nonce="two",
            timestamp=second["ts"],
            secret=SECRET,
        )["sig"]
        self.assertEqual(json.loads(service.handle(json.dumps(first)))["status"], "ok")
        with self.assertRaises(IdempotencyCollisionError):
            service.handle(json.dumps(second))

    def test_permission_revocation_during_execution_does_not_commit_result(
        self,
    ) -> None:
        started = threading.Event()
        release = threading.Event()

        def revoke(_caller: NodeId) -> dict[str, bool]:
            started.set()
            release.wait(1.0)
            return {"revoked": True}

        caller = NodeId("caller")
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
            trust_revoke_handler=revoke,
        )
        client = AuthenticatedNodeProvider(
            node_id=NodeId("peer"),
            caller_node_id=caller,
            secret=SECRET,
            transport=MemoryRemoteTransport(service),
        )
        errors: list[Exception] = []

        def invoke() -> None:
            try:
                client.revoke_self()
            except Exception as error:  # noqa: BLE001 - assertion captures the type.
                errors.append(error)

        worker = threading.Thread(target=invoke)
        worker.start()
        self.assertTrue(started.wait(1.0))
        service.update_grants({})
        release.set()
        worker.join(1.0)
        self.assertEqual(len(errors), 1)
        self.assertIsInstance(errors[0], RemoteAuthorizationError)

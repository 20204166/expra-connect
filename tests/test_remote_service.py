import json
import threading
import time
import unittest
from datetime import datetime, timezone

from expra_connect.cluster.models import CapabilityGrant
from expra_connect.identity import NodeId
from expra_connect.models import READ_CAPABILITIES, NodeCapability, NodePermission
from expra_connect.observability import ObservabilityWatcher
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
    RemoteExecutionError,
    RemoteService,
)
from expra_connect.server import RemoteSocketServer
from expra_connect.sharing import CapabilityShare
from expra_connect.socket_transport import RemoteTransportError, SocketRemoteTransport
from expra_connect.surfaces import SurfaceHandlerError, SurfaceRegistry
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
    def test_typed_surface_operations_dispatch_identity_and_registered_handler(self) -> None:
        caller = NodeId("caller")
        surfaces = SurfaceRegistry(clock=lambda: 10.0)
        seen: list[tuple[NodeId, dict[str, object]]] = []

        def read(peer: NodeId, params: dict[str, object]) -> object:
            seen.append((peer, params))
            return {"nested": ["host", params["section"]]}

        surfaces.register("dashboard/main", read=read)
        surfaces.grant_peer(caller, "dashboard/main", access="read")
        service = RemoteService(
            node_id=NodeId("peer"),
            display_name="Peer",
            hostname="peer-host",
            platform="Linux",
            status=NodeStatus.ONLINE,
            capabilities=READ_CAPABILITIES,
            provider=_Provider(),
            secret=SECRET,
            grants={caller: PeerGrant(caller, SECRET, frozenset({NodePermission.READ_STATE}))},
            surface_registry=surfaces,
        )
        client = AuthenticatedNodeProvider(
            node_id=NodeId("peer"),
            caller_node_id=caller,
            secret=SECRET,
            transport=MemoryRemoteTransport(service),
        )

        self.assertEqual(
            client.read_surface("dashboard/main", params={"section": "summary"}),
            {"nested": ["host", "summary"]},
        )
        self.assertEqual(seen, [(caller, {"section": "summary"})])
        surfaces.revoke_peer(caller, "dashboard/main")
        with self.assertRaises(RemoteAuthorizationError):
            client.read_surface("dashboard/main")

    def test_surface_dispatch_accepts_only_an_explicit_cluster_surface_source(self) -> None:
        caller = NodeId("caller")
        surfaces = SurfaceRegistry(clock=lambda: 10.0)
        surfaces.register("clustered", read=lambda _peer, _params: {"ok": True})
        cluster_grant = CapabilityGrant(
            caller,
            NodeId("peer"),
            frozenset({NodePermission.READ_STATE}),
            1.0,
            20.0,
        )
        service = RemoteService(
            node_id=NodeId("peer"),
            display_name="Peer",
            hostname="peer-host",
            platform="Linux",
            status=NodeStatus.ONLINE,
            capabilities=READ_CAPABILITIES,
            provider=_Provider(),
            secret=SECRET,
            clock=lambda: 10.0,
            grants={caller: PeerGrant(caller, SECRET, frozenset({NodePermission.READ_STATE}))},
            cluster_capability_grants=(cluster_grant,),
            surface_registry=surfaces,
        )
        client = AuthenticatedNodeProvider(
            node_id=NodeId("peer"),
            caller_node_id=caller,
            secret=SECRET,
            transport=MemoryRemoteTransport(service),
            clock=lambda: 10.0,
        )
        with self.assertRaises(RemoteAuthorizationError):
            client.read_surface("clustered")
        surfaces.grant_cluster(caller, "clustered", access="read", expires_at=20.0)
        self.assertEqual(client.read_surface("clustered"), {"ok": True})

    def test_surface_review_and_named_action_require_their_own_grants(self) -> None:
        caller = NodeId("caller")
        surfaces = SurfaceRegistry()
        surfaces.register(
            "settings",
            read=lambda _peer, _params: {"read": True},
            review=lambda _peer, _params: {"review": True},
            actions={"save": lambda _peer, params: {"saved": params["value"]}},
        )
        surfaces.grant_peer(caller, "settings", access="review")
        surfaces.grant_peer(caller, "settings", access="action")
        service = RemoteService(
            node_id=NodeId("peer"),
            display_name="Peer",
            hostname="peer-host",
            platform="Linux",
            status=NodeStatus.ONLINE,
            capabilities=READ_CAPABILITIES,
            provider=_Provider(),
            secret=SECRET,
            grants={caller: PeerGrant(caller, SECRET, frozenset({NodePermission.READ_STATE}))},
            surface_registry=surfaces,
        )
        client = AuthenticatedNodeProvider(
            node_id=NodeId("peer"),
            caller_node_id=caller,
            secret=SECRET,
            transport=MemoryRemoteTransport(service),
        )

        self.assertEqual(client.review_surface("settings"), {"review": True})
        self.assertEqual(
            client.invoke_surface_action("settings", "save", params={"value": 3}),
            {"saved": 3},
        )
        with self.assertRaises(RemoteAuthorizationError):
            client.read_surface("settings")

    def test_surface_denial_expiry_unknown_and_handler_errors_are_typed(self) -> None:
        now = [10.0]
        caller = NodeId("caller")
        surfaces = SurfaceRegistry(clock=lambda: now[0])
        surfaces.register(
            "settings",
            read=lambda _peer, _params: (_ for _ in ()).throw(
                SurfaceHandlerError("private handler detail")
            ),
            actions={"save": lambda _peer, _params: {"ok": True}},
        )
        surfaces.grant_peer(caller, "settings", access="read", expires_at=10.0)
        surfaces.grant_peer(caller, "settings", access="action")
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
            grants={caller: PeerGrant(caller, SECRET, frozenset({NodePermission.READ_STATE}))},
            surface_registry=surfaces,
        )
        client = AuthenticatedNodeProvider(
            node_id=NodeId("peer"),
            caller_node_id=caller,
            secret=SECRET,
            transport=MemoryRemoteTransport(service),
            clock=lambda: now[0],
        )

        with self.assertRaises(RemoteExecutionError) as handler_error:
            client.read_surface("settings")
        self.assertEqual(str(handler_error.exception), "execution_failed")
        self.assertNotIn("private", str(handler_error.exception))
        with self.assertRaises(RemoteExecutionError) as unknown_error:
            client.invoke_surface_action("settings", "missing")
        self.assertEqual(str(unknown_error.exception), "execution_failed")
        now[0] = 11.0
        with self.assertRaises(RemoteAuthorizationError):
            client.read_surface("settings")
        with self.assertRaises(RemoteAuthorizationError):
            client.read_surface("missing")

    def test_service_receives_surface_registry_and_live_cluster_grants(self) -> None:
        surfaces = SurfaceRegistry()
        service = RemoteService(
            node_id=NodeId("peer"),
            display_name="Peer",
            hostname="peer-host",
            platform="Linux",
            status=NodeStatus.ONLINE,
            capabilities=READ_CAPABILITIES,
            provider=_Provider(),
            secret=SECRET,
            surface_registry=surfaces,
        )

        self.assertIs(service._surface_registry, surfaces)
        grant = CapabilityGrant(
            NodeId("caller"),
            NodeId("peer"),
            frozenset({NodePermission.READ_STATE}),
            1.0,
            20.0,
        )
        service.update_cluster_capability_grants((grant,))
        self.assertEqual(service._cluster_capability_grants, (grant,))

    def test_authenticated_provider_observes_each_remote_operation_once(self) -> None:
        observer = ObservabilityWatcher()
        service_observer = ObservabilityWatcher()
        service = RemoteService(
            node_id=NodeId("peer"),
            display_name="Peer",
            hostname="peer-host",
            platform="Linux",
            status=NodeStatus.ONLINE,
            capabilities=READ_CAPABILITIES,
            provider=_Provider(),
            secret=SECRET,
            observer=service_observer,
        )
        client = AuthenticatedNodeProvider(
            node_id=NodeId("peer"),
            secret=SECRET,
            transport=MemoryRemoteTransport(
                service
            ),
            observer=observer,
        )

        client.hello()
        cancelled = threading.Event()
        cancelled.set()
        with self.assertRaises(RemoteExecutionError):
            client.hello(cancelled)
        client.invalidate()
        with self.assertRaises(RemoteAuthError):
            client.hello()

        metrics = {metric.target: metric for metric in observer.snapshot().metrics}
        self.assertEqual(set(metrics), {"remote:hello"})
        self.assertEqual(metrics["remote:hello"].count, 3)
        self.assertEqual(metrics["remote:hello"].successes, 1)
        self.assertEqual(metrics["remote:hello"].failures, 1)
        self.assertEqual(metrics["remote:hello"].cancellations, 1)
        self.assertNotIn(SECRET, metrics["remote:hello"].last_error or "")
        service_metrics = {
            metric.target: metric for metric in service_observer.snapshot().metrics
        }
        self.assertEqual(set(service_metrics), {"service:hello"})
        self.assertEqual(service_metrics["service:hello"].count, 1)

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

    def test_authenticated_logical_session_reuses_session(self) -> None:
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

    def test_expired_session_is_rejected_by_current_generation_check(self) -> None:
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
        )
        client = AuthenticatedNodeProvider(
            node_id=NodeId("peer"),
            secret=SECRET,
            transport=MemoryRemoteTransport(service),
            clock=lambda: now[0],
        )
        client.hello()
        session_id = client._session_id
        self.assertIsNotNone(session_id)
        now[0] = 701.0
        with self.assertRaises(RemoteAuthError):
            service._sessions.assert_current(session_id or "", "memory")

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

import json
import threading
import time
import unittest
from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any, cast

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
    RemoteUnavailableError,
)
from expra_connect.server import RemoteSocketServer
from expra_connect.sharing import CapabilityShare
from expra_connect.socket_transport import RemoteTransportError, SocketRemoteTransport
from expra_connect.surface_provider import dispatch_surface_request
from expra_connect.surfaces import SurfaceHandlerError, SurfaceRegistry
from expra_connect.wire_protocol import (
    IdempotencyCache,
    IdempotencyCollisionError,
    PeerGrant,
    RemoteProtocolError,
    RemoteRequest,
    sign_request,
    sign_response,
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
    def _service(
        self,
        *,
        capabilities: frozenset[NodeCapability] = READ_CAPABILITIES,
        permissions: frozenset[NodePermission] | None = None,
        clock: Callable[[], float] = time.time,
        grants: dict[NodeId, PeerGrant] | None = None,
        cluster_capability_grants: tuple[CapabilityGrant, ...] = (),
        surface_registry: SurfaceRegistry | None = None,
        idempotency_cache: IdempotencyCache | None = None,
    ) -> RemoteService:
        return RemoteService(
            node_id=NodeId("peer"),
            display_name="Peer",
            hostname="peer-host",
            platform="Linux",
            status=NodeStatus.ONLINE,
            capabilities=capabilities,
            provider=_Provider(),
            secret=SECRET,
            clock=clock,
            permissions=permissions,
            grants=grants,
            cluster_capability_grants=cluster_capability_grants,
            surface_registry=surface_registry,
            idempotency_cache=idempotency_cache,
        )

    def _surface_service(
        self,
        surfaces: SurfaceRegistry,
        caller: NodeId,
        *,
        capabilities: frozenset[NodeCapability] = READ_CAPABILITIES,
        permissions: frozenset[NodePermission] = frozenset({NodePermission.READ_STATE}),
        clock: Callable[[], float] = time.time,
        cluster_capability_grants: tuple[CapabilityGrant, ...] = (),
    ) -> RemoteService:
        return self._service(
            capabilities=capabilities,
            clock=clock,
            permissions=permissions,
            grants={caller: PeerGrant(caller, SECRET, permissions)},
            cluster_capability_grants=cluster_capability_grants,
            surface_registry=surfaces,
        )

    def _surface_client(
        self,
        service: RemoteService,
        caller: NodeId,
        *,
        clock: Callable[[], float] = time.time,
    ) -> AuthenticatedNodeProvider:
        return self._client(service, caller=caller, clock=clock)

    def _client(
        self,
        service: RemoteService | None = None,
        *,
        caller: NodeId | None = None,
        clock: Callable[[], float] = time.time,
    ) -> AuthenticatedNodeProvider:
        target = self._service() if service is None else service
        return AuthenticatedNodeProvider(
            node_id=NodeId("peer"),
            caller_node_id=caller,
            secret=SECRET,
            transport=MemoryRemoteTransport(target),
            clock=clock,
        )

    def test_typed_surface_operations_dispatch_identity_and_registered_handler(
        self,
    ) -> None:
        caller = NodeId("caller")
        surfaces = SurfaceRegistry(clock=lambda: 10.0)
        seen: list[tuple[NodeId, dict[str, object]]] = []

        def read(peer: NodeId, params: dict[str, object]) -> object:
            seen.append((peer, params))
            return {"nested": ["host", params["section"]]}

        surfaces.register("dashboard/main", read=read)
        surfaces.grant_peer(caller, "dashboard/main", access="read")
        service = self._surface_service(surfaces, caller)
        client = self._surface_client(service, caller)

        self.assertEqual(
            client.read_surface("dashboard/main", params={"section": "summary"}),
            {"nested": ["host", "summary"]},
        )
        self.assertEqual(seen, [(caller, {"section": "summary"})])
        surfaces.revoke_peer(caller, "dashboard/main")
        with self.assertRaises(RemoteAuthorizationError):
            client.read_surface("dashboard/main")

    def test_surface_dispatch_accepts_only_an_explicit_cluster_surface_source(
        self,
    ) -> None:
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
        clock = lambda: 10.0
        service = self._surface_service(
            surfaces,
            caller,
            clock=clock,
            cluster_capability_grants=(cluster_grant,),
        )
        client = self._surface_client(service, caller, clock=clock)
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
        service = self._surface_service(surfaces, caller)
        client = self._surface_client(service, caller)

        self.assertEqual(client.review_surface("settings"), {"review": True})
        with self.assertRaises(RemoteAuthorizationError):
            client.invoke_surface_action("settings", "save", params={"value": 3})
        with self.assertRaises(RemoteAuthorizationError):
            client.read_surface("settings")

        management_service = self._surface_service(
            surfaces,
            caller,
            capabilities=frozenset(
                {NodeCapability.READ_STATE, NodeCapability.REMOTE_MANAGEMENT}
            ),
            permissions=frozenset({NodePermission.REMOTE_MANAGEMENT}),
        )
        management_client = self._surface_client(management_service, caller)
        self.assertEqual(
            management_client.invoke_surface_action(
                "settings", "save", params={"value": 3}
            ),
            {"saved": 3},
        )

    def test_surface_action_requires_remote_management_and_action_grant(self) -> None:
        caller = NodeId("caller")
        surfaces = SurfaceRegistry()
        surfaces.register(
            "settings",
            read=lambda _peer, _params: {},
            actions={"save": lambda _peer, _params: {"ok": True}},
        )
        surfaces.grant_peer(caller, "settings", access="action")
        service = self._surface_service(
            surfaces,
            caller,
            capabilities=frozenset(
                {NodeCapability.READ_STATE, NodeCapability.REMOTE_MANAGEMENT}
            ),
        )
        read_client = self._surface_client(service, caller)
        with self.assertRaises(RemoteAuthorizationError):
            read_client.invoke_surface_action("settings", "save")

        surfaces.revoke_peer(caller, "settings", access="action")
        management_service = self._surface_service(
            surfaces,
            caller,
            capabilities=frozenset(
                {NodeCapability.READ_STATE, NodeCapability.REMOTE_MANAGEMENT}
            ),
            permissions=frozenset({NodePermission.REMOTE_MANAGEMENT}),
        )
        management_client = self._surface_client(management_service, caller)
        with self.assertRaises(RemoteAuthorizationError):
            management_client.invoke_surface_action("settings", "save")

    def test_surface_dispatch_rejects_unknown_or_unrelated_cluster_grants(self) -> None:
        caller = NodeId("caller")
        target = NodeId("target")
        surfaces = SurfaceRegistry()
        surfaces.register("clustered", read=lambda _peer, _params: {"ok": True})
        request = RemoteRequest(
            node_id=target,
            caller_node_id=caller,
            op="surface_read",
            params={"surface_id": "clustered"},
            request_id="request",
            nonce="nonce",
            timestamp=1.0,
        )
        unrelated = CapabilityGrant(
            NodeId("other"),
            target,
            frozenset({NodePermission.READ_STATE}),
            0.0,
            20.0,
        )
        surfaces.grant_cluster(caller, "clustered", access="read")
        with self.assertRaises(RemoteAuthorizationError):
            dispatch_surface_request(surfaces, request, unrelated)
        wrong_target = CapabilityGrant(
            caller,
            NodeId("other-target"),
            frozenset({NodePermission.READ_STATE}),
            0.0,
            20.0,
        )
        with self.assertRaises(RemoteAuthorizationError):
            dispatch_surface_request(surfaces, request, wrong_target)

        unknown = RemoteRequest(
            node_id=target,
            caller_node_id=caller,
            op="surface_delete",
            params={"surface_id": "clustered"},
            request_id="request-2",
            nonce="nonce-2",
            timestamp=1.0,
        )
        with self.assertRaises(RemoteProtocolError):
            dispatch_surface_request(surfaces, unknown, None)
        missing_caller = RemoteRequest(
            node_id=target,
            caller_node_id=None,
            op="surface_read",
            params={"surface_id": "clustered"},
            request_id="request-3",
            nonce="nonce-3",
            timestamp=1.0,
        )
        with self.assertRaises(RemoteAuthorizationError):
            dispatch_surface_request(surfaces, missing_caller, None)
        with self.assertRaises(RemoteUnavailableError):
            dispatch_surface_request(None, request, None)

    def test_cluster_surface_action_requires_pair_and_cluster_intersection(
        self,
    ) -> None:
        caller = NodeId("caller")
        target = NodeId("peer")
        surfaces = SurfaceRegistry()
        surfaces.register(
            "clustered",
            read=lambda _peer, _params: {"read": True},
            actions={"save": lambda _peer, _params: {"saved": True}},
        )
        surfaces.grant_cluster(caller, "clustered", access="action")
        cluster_grant = CapabilityGrant(
            caller,
            target,
            frozenset({NodePermission.REMOTE_MANAGEMENT}),
            1.0,
            20.0,
        )

        def make_service(
            pair_permissions: frozenset[NodePermission],
            capability_permissions: frozenset[NodePermission],
        ) -> RemoteService:
            return self._surface_service(
                surfaces,
                caller,
                capabilities=frozenset(
                    {NodeCapability.READ_STATE, NodeCapability.REMOTE_MANAGEMENT}
                ),
                permissions=pair_permissions,
                clock=lambda: 10.0,
                cluster_capability_grants=(
                    CapabilityGrant(
                        caller,
                        target,
                        capability_permissions,
                        1.0,
                        20.0,
                    ),
                ),
            )

        clock = lambda: 10.0
        allowed = self._surface_client(
            make_service(
                frozenset({NodePermission.REMOTE_MANAGEMENT}),
                cluster_grant.permissions,
            ),
            caller,
            clock=clock,
        )
        self.assertEqual(
            allowed.invoke_surface_action("clustered", "save"), {"saved": True}
        )

        read_pair = self._surface_client(
            make_service(
                frozenset({NodePermission.READ_STATE}),
                cluster_grant.permissions,
            ),
            caller,
            clock=clock,
        )
        with self.assertRaises(RemoteAuthorizationError):
            read_pair.invoke_surface_action("clustered", "save")

        read_cluster = self._surface_client(
            make_service(
                frozenset({NodePermission.REMOTE_MANAGEMENT}),
                frozenset({NodePermission.READ_STATE}),
            ),
            caller,
            clock=clock,
        )
        with self.assertRaises(RemoteAuthorizationError):
            read_cluster.invoke_surface_action("clustered", "save")

    def test_surface_provider_rejects_invalid_params_without_network_request(
        self,
    ) -> None:
        calls = 0

        class CountingTransport:
            def request(self, _envelope: str, _cancel: object = None) -> str:
                nonlocal calls
                calls += 1
                raise AssertionError("invalid surface input reached transport")

        client = AuthenticatedNodeProvider(
            node_id=NodeId("peer"),
            secret=SECRET,
            transport=CountingTransport(),
        )
        calls_by_operation: tuple[tuple[str, Callable[[Any], object]], ...] = (
            ("read", lambda params: client.read_surface("surface", params=params)),
            (
                "review",
                lambda params: client.review_surface("surface", params=params),
            ),
            (
                "action",
                lambda params: client.invoke_surface_action(
                    "surface", "save", params=params
                ),
            ),
        )
        for operation, invoke in calls_by_operation:
            for params in ([], "", False, object()):
                with (
                    self.subTest(
                        operation=operation, params_type=type(params).__name__
                    ),
                    self.assertRaises(RemoteProtocolError),
                ):
                    invoke(params)
        cyclic: dict[str, object] = {}
        cyclic["self"] = cyclic
        with self.assertRaises(RemoteProtocolError):
            client.read_surface("surface", params=cyclic)
        self.assertEqual(calls, 0)

    def test_surface_provider_accepts_opaque_results_and_rejects_bad_outer_payloads(
        self,
    ) -> None:
        client = AuthenticatedNodeProvider(
            node_id=NodeId("peer"), secret=SECRET, transport=object()
        )
        results: tuple[object, ...] = (None, {}, [], False)
        for result in results:
            cast(Any, client)._request = (
                lambda _operation, _params, _cancel=None, result=result: {
                    "result": result
                }
            )
            self.assertIs(client.read_surface("surface"), result)
        payloads: tuple[object, ...] = ({}, None, [], "text")
        for payload in payloads:
            cast(Any, client)._request = (
                lambda _operation, _params, _cancel=None, payload=payload: payload
            )
            with (
                self.subTest(payload_type=type(payload).__name__),
                self.assertRaises(RemoteProtocolError),
            ):
                client.read_surface("surface")

    def test_surface_provider_snapshots_top_level_params_and_normalizes_none(
        self,
    ) -> None:
        captured: list[tuple[str, dict[str, object]]] = []
        client = AuthenticatedNodeProvider(
            node_id=NodeId("peer"), secret=SECRET, transport=object()
        )

        def request(
            operation: str,
            params: dict[str, object],
            _cancel: object = None,
        ) -> dict[str, object]:
            captured.append((operation, params))
            return {"result": True}

        cast(Any, client)._request = request
        original = {"value": {"nested": True}}
        client.read_surface("surface", params=original)
        client.review_surface("surface", params=None)
        client.invoke_surface_action("surface", "save", params=None)
        self.assertEqual(captured[0][1]["params"], original)
        self.assertIsNot(captured[0][1]["params"], original)
        self.assertEqual(captured[1][1]["params"], {})
        self.assertEqual(captured[2][1]["params"], {})

    def test_surface_cancellation_before_dispatch_makes_no_request(self) -> None:
        calls = 0

        class CountingTransport:
            def request(self, _envelope: str, _cancel: object = None) -> str:
                nonlocal calls
                calls += 1
                raise AssertionError("cancelled surface reached transport")

        cancelled = threading.Event()
        cancelled.set()
        client = AuthenticatedNodeProvider(
            node_id=NodeId("peer"),
            secret=SECRET,
            transport=CountingTransport(),
        )
        operations: tuple[Callable[[], object], ...] = (
            lambda: client.read_surface("surface", cancel_event=cancelled),
            lambda: client.review_surface("surface", cancel_event=cancelled),
            lambda: client.invoke_surface_action(
                "surface", "save", cancel_event=cancelled
            ),
        )
        for operation in operations:
            with self.assertRaises(RemoteExecutionError):
                operation()
        self.assertEqual(calls, 0)

    def test_lost_surface_action_response_is_not_retried(self) -> None:
        caller = NodeId("caller")
        calls = 0
        surfaces = SurfaceRegistry()

        def save(_peer: NodeId, _params: dict[str, object]) -> object:
            nonlocal calls
            calls += 1
            return {"saved": True}

        surfaces.register(
            "settings", read=lambda _peer, _params: {}, actions={"save": save}
        )
        surfaces.grant_peer(caller, "settings", access="action")
        service = self._surface_service(
            surfaces,
            caller,
            capabilities=frozenset(
                {NodeCapability.READ_STATE, NodeCapability.REMOTE_MANAGEMENT}
            ),
            permissions=frozenset({NodePermission.REMOTE_MANAGEMENT}),
        )

        class LoseResponse:
            def __init__(self) -> None:
                self.calls = 0

            def request(self, envelope: str, _cancel: object = None) -> str:
                self.calls += 1
                service.handle(envelope)
                raise RemoteTransportError("response lost")

        transport = LoseResponse()
        client = AuthenticatedNodeProvider(
            node_id=NodeId("peer"),
            caller_node_id=caller,
            secret=SECRET,
            transport=transport,
        )
        with self.assertRaises(RemoteTransportError):
            client.invoke_surface_action("settings", "save")
        self.assertEqual(transport.calls, 1)
        self.assertEqual(calls, 1)

    def test_exact_surface_action_replay_uses_existing_idempotency(self) -> None:
        caller = NodeId("caller")
        calls = 0
        surfaces = SurfaceRegistry()

        def save(_peer: NodeId, _params: dict[str, object]) -> object:
            nonlocal calls
            calls += 1
            return {"saved": calls}

        surfaces.register(
            "settings", read=lambda _peer, _params: {}, actions={"save": save}
        )
        surfaces.grant_peer(caller, "settings", access="action")
        service = self._surface_service(
            surfaces,
            caller,
            capabilities=frozenset(
                {NodeCapability.READ_STATE, NodeCapability.REMOTE_MANAGEMENT}
            ),
            permissions=frozenset({NodePermission.REMOTE_MANAGEMENT}),
        )
        first = sign_request(
            node_id="peer",
            caller_node_id="caller",
            op="surface_action",
            params={"surface_id": "settings", "action": "save", "params": {}},
            request_id="same-action",
            nonce="first-nonce",
            timestamp=time.time(),
            secret=SECRET,
        )
        second = dict(first)
        second["nonce"] = "second-nonce"
        second["sig"] = sign_request(
            node_id="peer",
            caller_node_id="caller",
            op="surface_action",
            params=first["params"],
            request_id="same-action",
            nonce="second-nonce",
            timestamp=first["ts"],
            secret=SECRET,
        )["sig"]
        first_response = json.loads(service.handle(json.dumps(first)))
        second_response = json.loads(service.handle(json.dumps(second)))
        self.assertEqual(first_response["payload"], second_response["payload"])
        self.assertEqual(calls, 1)

    def test_failed_surface_action_replay_does_not_reexecute(self) -> None:
        caller = NodeId("caller")
        calls = 0
        surfaces = SurfaceRegistry()

        def fail(_peer: NodeId, _params: dict[str, object]) -> object:
            nonlocal calls
            calls += 1
            raise RuntimeError("action failed")

        surfaces.register(
            "settings", read=lambda _peer, _params: {}, actions={"save": fail}
        )
        surfaces.grant_peer(caller, "settings", access="action")
        service = self._surface_service(
            surfaces,
            caller,
            capabilities=frozenset(
                {NodeCapability.READ_STATE, NodeCapability.REMOTE_MANAGEMENT}
            ),
            permissions=frozenset({NodePermission.REMOTE_MANAGEMENT}),
        )
        request = sign_request(
            node_id="peer",
            caller_node_id="caller",
            op="surface_action",
            params={"surface_id": "settings", "action": "save", "params": {}},
            request_id="failed-action",
            nonce="nonce",
            timestamp=time.time(),
            secret=SECRET,
        )

        first = json.loads(service.handle(json.dumps(request)))
        second = json.loads(service.handle(json.dumps(request)))

        self.assertEqual(first["error"], "execution_failed")
        self.assertEqual(second["error"], "execution_failed")
        self.assertEqual(calls, 1)

    def test_read_retry_can_reuse_an_envelope_after_response_loss(self) -> None:
        service = self._service()

        class LoseFirstResponse:
            def __init__(self) -> None:
                self.calls = 0

            def request(self, envelope: str, _cancel: object = None) -> str:
                self.calls += 1
                response = service.handle(envelope)
                if self.calls == 1:
                    raise RemoteTransportError("response lost")
                return response

        transport = LoseFirstResponse()
        client = AuthenticatedNodeProvider(
            node_id=NodeId("peer"),
            secret=SECRET,
            transport=transport,
        )

        response = client.hello()

        self.assertEqual(response["node_id"], "peer")
        self.assertEqual(transport.calls, 2)

    def test_concurrent_duplicate_request_executes_handler_once(self) -> None:
        caller = NodeId("caller")
        started = threading.Event()
        release = threading.Event()
        second_started = threading.Event()
        second_run = threading.Event()
        second_waiting = threading.Event()
        calls = 0

        class WaitingEvent(threading.Event):
            def wait(self, timeout: float | None = None) -> bool:
                second_waiting.set()
                return super().wait(timeout)

        class SignalingIdempotencyCache(IdempotencyCache):
            def __bool__(self) -> bool:
                return True

            def run(
                self,
                key: tuple[str, str],
                fingerprint: str,
                operation: Callable[[], dict[str, Any]],
            ) -> dict[str, Any]:
                with self._lock:
                    entry = self._entries.get(key)
                    if entry is not None and not entry.complete:
                        second_run.set()
                        entry.event = WaitingEvent()
                return super().run(key, fingerprint, operation)

        def revoke(_caller: NodeId) -> dict[str, bool]:
            nonlocal calls
            calls += 1
            started.set()
            if not release.wait(1.0):
                raise AssertionError("blocking handler was not released")
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
                caller: PeerGrant(
                    caller, SECRET, frozenset({NodePermission.READ_STATE})
                )
            },
            idempotency_cache=SignalingIdempotencyCache(),
            trust_revoke_handler=revoke,
        )
        envelope = json.dumps(
            sign_request(
                node_id="peer",
                caller_node_id="caller",
                op="revoke_self",
                params={},
                request_id="concurrent-revoke",
                nonce="concurrent-nonce",
                timestamp=time.time(),
                secret=SECRET,
            )
        )
        responses: list[str] = []
        errors: list[BaseException] = []

        def handle(started_event: threading.Event | None = None) -> None:
            if started_event is not None:
                started_event.set()
            try:
                responses.append(service.handle(envelope))
            except BaseException as error:  # noqa: BLE001 - assertion captures errors.
                errors.append(error)

        first = threading.Thread(target=handle, daemon=True)
        second = threading.Thread(target=handle, args=(second_started,), daemon=True)
        first_thread_started = False
        second_thread_started = False
        try:
            first.start()
            first_thread_started = True
            self.assertTrue(started.wait(1.0))
            second.start()
            second_thread_started = True
            self.assertTrue(second_started.wait(1.0))
            self.assertTrue(second_run.wait(1.0))
            self.assertTrue(second_waiting.wait(1.0))
            self.assertFalse(release.is_set())
        finally:
            release.set()
            if first_thread_started:
                first.join(1.0)
            if second_thread_started:
                second.join(1.0)

        self.assertFalse(first.is_alive())
        self.assertFalse(second.is_alive())
        self.assertEqual(errors, [])
        self.assertEqual(len(responses), 2)
        self.assertEqual(
            [json.loads(response)["status"] for response in responses], ["ok", "ok"]
        )
        self.assertEqual(calls, 1)

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
        clock = lambda: now[0]
        service = self._surface_service(
            surfaces,
            caller,
            capabilities=frozenset(
                {NodeCapability.READ_STATE, NodeCapability.REMOTE_MANAGEMENT}
            ),
            permissions=frozenset(
                {NodePermission.READ_STATE, NodePermission.REMOTE_MANAGEMENT}
            ),
            clock=clock,
        )
        client = self._surface_client(service, caller, clock=clock)

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

    def test_review_and_action_handler_errors_are_redacted(self) -> None:
        caller = NodeId("caller")
        surfaces = SurfaceRegistry()

        def fail_review(_peer: NodeId, _params: dict[str, object]) -> object:
            raise RuntimeError("review secret detail")

        def fail_action(_peer: NodeId, _params: dict[str, object]) -> object:
            raise RuntimeError("action secret detail")

        surfaces.register(
            "settings",
            read=lambda _peer, _params: {"ok": True},
            review=fail_review,
            actions={"save": fail_action},
        )
        surfaces.grant_peer(caller, "settings", access="review")
        surfaces.grant_peer(caller, "settings", access="action")
        service = self._surface_service(
            surfaces,
            caller,
            capabilities=frozenset(
                {NodeCapability.READ_STATE, NodeCapability.REMOTE_MANAGEMENT}
            ),
            permissions=frozenset(
                {NodePermission.READ_STATE, NodePermission.REMOTE_MANAGEMENT}
            ),
        )
        client = self._surface_client(service, caller)
        operations: tuple[Callable[[], object], ...] = (
            lambda: client.review_surface("settings"),
            lambda: client.invoke_surface_action("settings", "save"),
        )
        for operation in operations:
            with (
                self.subTest(operation=operation),
                self.assertRaises(RemoteExecutionError) as error,
            ):
                operation()
            self.assertEqual(str(error.exception), "execution_failed")
            self.assertNotIn("secret detail", str(error.exception))

    def test_non_json_surface_result_becomes_stable_execution_error(self) -> None:
        caller = NodeId("caller")
        surfaces = SurfaceRegistry()
        surfaces.register("bad", read=lambda _peer, _params: object())
        surfaces.grant_peer(caller, "bad", access="read")
        service = self._surface_service(surfaces, caller)
        client = self._surface_client(service, caller)
        with self.assertRaises(RemoteExecutionError) as error:
            client.read_surface("bad")
        self.assertEqual(str(error.exception), "execution_failed")

    def test_service_receives_surface_registry_and_live_cluster_grants(self) -> None:
        surfaces = SurfaceRegistry()
        service = self._service(surface_registry=surfaces)

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

    def test_service_preserves_explicit_empty_idempotency_cache(self) -> None:
        cache = IdempotencyCache()

        service = self._service(idempotency_cache=cache)

        self.assertIs(service._idempotency, cache)

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
            transport=MemoryRemoteTransport(service),
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

    def test_service_handler_rejected_by_grant_fence_is_not_success(self) -> None:
        observer = ObservabilityWatcher()
        service_holder: list[RemoteService] = []

        class _MutatingProvider:
            def dashboard_snapshot(self) -> DashboardSnapshot:
                service_holder[0].update_grants({})
                return DashboardSnapshot(
                    system_label="peer",
                    scanned_at=datetime.now(timezone.utc),
                    resources=(
                        ResourceSummary("cpu", "CPU", "10%", "ok", 10.0, ("ok",)),
                    ),
                )

        service = RemoteService(
            node_id=NodeId("peer"),
            display_name="Peer",
            hostname="peer-host",
            platform="Linux",
            status=NodeStatus.ONLINE,
            capabilities=READ_CAPABILITIES,
            provider=_MutatingProvider(),
            secret=SECRET,
            grants={
                NodeId("caller"): PeerGrant(
                    NodeId("caller"), SECRET, frozenset({NodePermission.READ_STATE})
                )
            },
            observer=observer,
        )
        service_holder.append(service)
        request = sign_request(
            node_id="peer",
            caller_node_id="caller",
            op="dashboard_snapshot",
            params={},
            request_id="commit-fence",
            nonce="commit-fence-nonce",
            timestamp=time.time(),
            secret=SECRET,
        )
        response = json.loads(service.handle(json.dumps(request)))
        self.assertEqual(response["status"], "error")
        metric = observer.snapshot().metrics[0]
        self.assertEqual(metric.target, "service:dashboard_snapshot")
        self.assertEqual(metric.failures, 1)
        self.assertEqual(metric.successes, 0)

    def test_saturated_observer_does_not_affect_service_result(self) -> None:
        observer = ObservabilityWatcher(max_active=1)
        observer.begin("pre-saturate")
        service = RemoteService(
            node_id=NodeId("peer"),
            display_name="Peer",
            hostname="peer-host",
            platform="Linux",
            status=NodeStatus.ONLINE,
            capabilities=READ_CAPABILITIES,
            provider=_Provider(),
            secret=SECRET,
            observer=observer,
        )
        client = AuthenticatedNodeProvider(
            node_id=NodeId("peer"),
            secret=SECRET,
            transport=MemoryRemoteTransport(service),
            observer=observer,
        )
        hello = client.hello()
        self.assertEqual(hello["node_id"], "peer")

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

    def test_old_coordinator_request_is_fenced_after_failover(self) -> None:
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
            coordinator_epoch=8,
            fencing_token="new-fence",
        )
        stale = RemoteRequest(
            node_id=NodeId("peer"),
            caller_node_id=NodeId("old-coordinator"),
            op="pause_worker",
            params={
                "target_node_id": "worker",
                "cluster_id": "cluster",
                "epoch": 7,
                "fencing_token": "old-fence",
            },
            request_id="request",
            nonce="nonce",
            timestamp=0.0,
        )
        with self.assertRaises(RemoteAuthorizationError):
            service._verify_role_fence(stale)
        wrong_token = RemoteRequest(
            node_id=NodeId("peer"),
            caller_node_id=NodeId("coordinator"),
            op="pause_worker",
            params={
                "target_node_id": "worker",
                "cluster_id": "cluster",
                "epoch": 8,
                "fencing_token": "old-fence",
            },
            request_id="request",
            nonce="nonce",
            timestamp=0.0,
        )
        with self.assertRaises(RemoteAuthorizationError):
            service._verify_role_fence(wrong_token)

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

    def test_pair_revocation_remains_outer_gate_for_shared_capability(self) -> None:
        share = CapabilityShare()
        caller = NodeId("caller")
        share.register("demo.read_state", lambda _peer, _params: {"ok": True})
        share.allow(caller, "demo.read_state")
        grant = PeerGrant(caller, SECRET, frozenset({NodePermission.READ_STATE}))
        service = RemoteService(
            node_id=NodeId("peer"),
            display_name="Peer",
            hostname="peer-host",
            platform="Linux",
            status=NodeStatus.ONLINE,
            capabilities=READ_CAPABILITIES,
            provider=_Provider(),
            secret=SECRET,
            grants={caller: grant},
            capability_share=share,
        )
        client = AuthenticatedNodeProvider(
            node_id=NodeId("peer"),
            caller_node_id=caller,
            secret=SECRET,
            transport=MemoryRemoteTransport(service),
        )
        self.assertEqual(client.request_shared("demo.read_state"), {"ok": True})

        service.update_grants({})

        with self.assertRaises(RemoteAuthError):
            client.request_shared("demo.read_state")

    def test_shared_handler_failure_keeps_stable_remote_error(self) -> None:
        share = CapabilityShare()
        caller = NodeId("caller")

        def broken(_peer: NodeId, _params: dict[str, object]) -> object:
            raise RuntimeError("private handler detail")

        share.register("demo.read_state", broken)
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
        with self.assertRaises(RemoteExecutionError) as error:
            client.request_shared("demo.read_state")
        self.assertEqual(str(error.exception), "execution_failed")
        self.assertNotIn("private handler detail", str(error.exception))

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
            session_clock=lambda: now[0],
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
            session_clock=lambda: now[0],
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
            service._sessions.assert_current(session_id or "", None)

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

    def test_in_flight_result_rejected_after_connection_resume(self) -> None:
        started = threading.Event()
        release = threading.Event()

        class BlockingProvider(_Provider):
            def component_summary(self, key: str) -> ResourceSummary:
                started.set()
                release.wait(1.0)
                return super().component_summary(key)

        caller = NodeId("caller")
        service = RemoteService(
            node_id=NodeId("peer"),
            display_name="Peer",
            hostname="peer-host",
            platform="Linux",
            status=NodeStatus.ONLINE,
            capabilities=READ_CAPABILITIES,
            provider=BlockingProvider(),
            secret=SECRET,
            grants={
                caller: PeerGrant(
                    caller, SECRET, frozenset({NodePermission.READ_STATE})
                )
            },
        )
        first = sign_request(
            node_id="peer",
            caller_node_id="caller",
            op="hello",
            params={},
            request_id="resume-first",
            nonce="resume-first-nonce",
            timestamp=time.time(),
            secret=SECRET,
        )
        first_response = json.loads(
            service.handle(json.dumps(first), connection_generation="generation-a")
        )
        session_id = first_response["session_id"]

        holder: dict[str, BaseException] = {}

        def invoke() -> None:
            request = sign_request(
                node_id="peer",
                caller_node_id="caller",
                op="component_summary",
                params={"key": "cpu"},
                request_id="resume-in-flight",
                nonce="resume-in-flight-nonce",
                timestamp=time.time(),
                secret=SECRET,
                session_id=session_id,
                resume=True,
            )
            try:
                service.handle(
                    json.dumps(request), connection_generation="generation-a"
                )
            except BaseException as error:  # noqa: BLE001 - assertion captures type
                holder["error"] = error

        worker = threading.Thread(target=invoke)
        worker.start()
        self.assertTrue(started.wait(1.0))
        resume = sign_request(
            node_id="peer",
            caller_node_id="caller",
            op="hello",
            params={},
            request_id="resume-second",
            nonce="resume-second-nonce",
            timestamp=time.time(),
            secret=SECRET,
            session_id=session_id,
            resume=True,
        )
        service.handle(json.dumps(resume), connection_generation="generation-b")
        release.set()
        worker.join(1.0)

        self.assertIsInstance(holder.get("error"), RemoteAuthError)

    def test_session_does_not_survive_service_restart(self) -> None:
        first_service = self._service()
        client = self._client(first_service)
        client.hello()
        session_id = client._session_id
        self.assertIsNotNone(session_id)

        restarted = self._service()
        resumed = sign_request(
            node_id="peer",
            op="hello",
            params={},
            request_id="restart-resume",
            nonce="restart-resume-nonce",
            timestamp=time.time(),
            secret=SECRET,
            session_id=session_id,
            resume=True,
        )
        with self.assertRaises(RemoteAuthError):
            restarted.handle(json.dumps(resumed))

    def test_revoked_grant_cannot_reuse_session(self) -> None:
        caller = NodeId("caller")
        service = self._service(
            grants={
                caller: PeerGrant(
                    caller, SECRET, frozenset({NodePermission.READ_STATE})
                )
            }
        )
        client = self._client(service, caller=caller)
        client.hello()
        self.assertIsNotNone(client._session_id)

        service.update_grants({})

        with self.assertRaises(RemoteAuthError):
            client.hello()


class ProviderRequestMechanicsTests(unittest.TestCase):
    class _ScriptedTransport:
        def __init__(self, builder: Callable[[dict[str, Any]], str]) -> None:
            self._builder = builder
            self.requests: list[dict[str, Any]] = []

        def request(self, envelope_text: str, cancel_event: Any | None = None) -> str:
            envelope = json.loads(envelope_text)
            self.requests.append(envelope)
            return self._builder(envelope)

    class _CountingTransport:
        def __init__(self) -> None:
            self.calls = 0

        def request(self, envelope_text: str, cancel_event: Any | None = None) -> str:
            self.calls += 1
            raise RemoteTransportError("transport down")

    def _client(self, transport: Any) -> AuthenticatedNodeProvider:
        return AuthenticatedNodeProvider(
            node_id=NodeId("peer"),
            secret=SECRET,
            transport=transport,
        )

    @staticmethod
    def _respond(
        request_id: str,
        *,
        node_id: str = "peer",
        status: str = "ok",
        payload: dict[str, Any] | None = None,
        error: str | None = None,
        session_id: str | None = None,
    ) -> str:
        return json.dumps(
            sign_response(
                node_id=node_id,
                request_id=request_id,
                status=status,
                payload=payload,
                error=error,
                secret=SECRET,
                timestamp=time.time(),
                session_id=session_id,
            )
        )

    def test_non_json_response_is_protocol_error(self) -> None:
        transport = self._ScriptedTransport(lambda _envelope: "not json")
        client = self._client(transport)
        with self.assertRaises(RemoteProtocolError) as error:
            client._request("hello", {})
        self.assertIn("not valid JSON", str(error.exception))

    def test_signed_response_from_wrong_node_is_auth_error(self) -> None:
        transport = self._ScriptedTransport(
            lambda envelope: self._respond(
                envelope["request_id"],
                node_id="other",
                payload={"node_id": "other"},
            )
        )
        client = self._client(transport)
        with self.assertRaises(RemoteAuthError) as error:
            client._request("hello", {})
        self.assertIn("wrong node", str(error.exception))

    def test_mismatched_request_id_is_auth_error(self) -> None:
        transport = self._ScriptedTransport(
            lambda _envelope: self._respond(
                "different-request-id", payload={"node_id": "peer"}
            )
        )
        client = self._client(transport)
        with self.assertRaises(RemoteAuthError) as error:
            client._request("hello", {})
        self.assertIn("request id does not match", str(error.exception))

    def test_ok_response_without_payload_is_protocol_error(self) -> None:
        transport = self._ScriptedTransport(
            lambda envelope: self._respond(envelope["request_id"], payload=None)
        )
        client = self._client(transport)
        with self.assertRaises(RemoteProtocolError) as error:
            client._request("hello", {})
        self.assertIn("no payload", str(error.exception))

    def test_capability_unavailable_is_authorization_error(self) -> None:
        transport = self._ScriptedTransport(
            lambda envelope: self._respond(
                envelope["request_id"],
                status="error",
                error="capability_unavailable",
            )
        )
        client = self._client(transport)
        with self.assertRaises(RemoteAuthorizationError) as error:
            client._request("hello", {})
        self.assertIs(type(error.exception), RemoteAuthorizationError)

    def test_target_offline_is_unavailable_error(self) -> None:
        transport = self._ScriptedTransport(
            lambda envelope: self._respond(
                envelope["request_id"],
                status="error",
                error="target_offline",
            )
        )
        client = self._client(transport)
        with self.assertRaises(RemoteUnavailableError) as error:
            client._request("hello", {})
        self.assertIs(type(error.exception), RemoteUnavailableError)
        self.assertIn("target is offline", str(error.exception))

    def test_error_without_detail_is_execution_error(self) -> None:
        transport = self._ScriptedTransport(
            lambda envelope: self._respond(
                envelope["request_id"],
                status="error",
                error=None,
            )
        )
        client = self._client(transport)
        with self.assertRaises(RemoteExecutionError) as error:
            client._request("hello", {})
        self.assertIs(type(error.exception), RemoteExecutionError)
        self.assertIn("remote operation failed", str(error.exception))

    def test_session_id_propagates_and_marks_resume(self) -> None:
        transport = self._ScriptedTransport(
            lambda envelope: self._respond(
                envelope["request_id"],
                payload={"node_id": "peer"},
                session_id="session-1",
            )
        )
        client = self._client(transport)
        self.assertIsNone(client._session_id)
        client._request("hello", {})
        self.assertEqual(client._session_id, "session-1")
        client._request("hello", {})
        first, second = transport.requests
        self.assertNotIn("session_id", first)
        self.assertFalse(first.get("resume", False))
        self.assertEqual(second["session_id"], "session-1")
        self.assertIs(second["resume"], True)

    def test_late_older_response_cannot_replace_newer_session_id(self) -> None:
        first_started = threading.Event()
        release_first = threading.Event()
        request_lock = threading.Lock()
        request_count = 0

        class ReorderedTransport:
            def request(_self, envelope_text: str, _cancel: Any = None) -> str:
                nonlocal request_count
                envelope = json.loads(envelope_text)
                with request_lock:
                    request_count += 1
                    request_number = request_count
                if request_number == 1:
                    first_started.set()
                    if not release_first.wait(5.0):
                        raise TimeoutError("test did not release the first response")
                    session_id = "session-older"
                else:
                    session_id = "session-newer"
                return ProviderRequestMechanicsTests._respond(
                    envelope["request_id"],
                    payload={"node_id": "peer"},
                    session_id=session_id,
                )

        client = self._client(ReorderedTransport())
        errors: list[Exception] = []

        def request() -> None:
            try:
                client._request("hello", {})
            except Exception as error:  # noqa: BLE001 - report worker failures.
                errors.append(error)

        older = threading.Thread(target=request)
        newer = threading.Thread(target=request)
        older.start()
        self.assertTrue(first_started.wait(1.0))
        newer.start()
        newer.join(5.0)
        self.assertFalse(newer.is_alive())
        self.assertEqual(client._session_id, "session-newer")

        release_first.set()
        older.join(5.0)
        self.assertFalse(older.is_alive())
        self.assertEqual(errors, [])
        self.assertEqual(client._session_id, "session-newer")

    def test_unsafe_operation_is_not_retried_but_safe_operation_is(self) -> None:
        unsafe_transport = self._CountingTransport()
        unsafe_client = self._client(unsafe_transport)
        with self.assertRaises(RemoteTransportError):
            unsafe_client._request("process_force_quit", {})
        self.assertEqual(unsafe_transport.calls, 1)

        safe_transport = self._CountingTransport()
        safe_client = self._client(safe_transport)
        with self.assertRaises(RemoteTransportError):
            safe_client._request("hello", {})
        self.assertEqual(safe_transport.calls, 2)

    def test_cancelled_request_never_reaches_transport(self) -> None:
        transport = self._ScriptedTransport(lambda _envelope: "unused")
        client = self._client(transport)
        cancelled = threading.Event()
        cancelled.set()
        with self.assertRaises(RemoteExecutionError):
            client.hello(cancelled)
        self.assertEqual(transport.requests, [])

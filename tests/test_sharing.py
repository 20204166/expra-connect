import unittest
from dataclasses import FrozenInstanceError
from threading import Barrier, Event, Thread

from expra_connect.identity import NodeId
from expra_connect.sharing import CapabilityShare
from expra_connect.surfaces import (
    SurfaceAccess,
    SurfaceHandlerError,
    SurfaceRegistry,
)


class SharingTests(unittest.TestCase):
    def test_target_owned_capability_requires_explicit_permission(self) -> None:
        share = CapabilityShare()
        share.register("demo.read_state", lambda _peer, _params: {"ok": True})
        with self.assertRaises(PermissionError):
            share.request(NodeId("peer"), "demo.read_state")
        share.allow(NodeId("peer"), "demo.read_state")
        self.assertEqual(share.request(NodeId("peer"), "demo.read_state"), {"ok": True})

    def test_request_preserves_an_explicit_empty_parameter_mapping(self) -> None:
        received: list[dict[str, object]] = []

        def handler(_peer: NodeId, params: dict[str, object]) -> object:
            received.append(params)
            return None

        params: dict[str, object] = {}
        share = CapabilityShare()
        share.register("demo.read_state", handler)
        share.allow(NodeId("peer"), "demo.read_state")
        share.request(NodeId("peer"), "demo.read_state", params)
        self.assertIs(received[0], params)

    def test_registration_validates_opaque_ids_and_handlers(self) -> None:
        share = CapabilityShare()
        handler = lambda _peer, _params: None
        share.register("app/foo.v1", handler)

        for invalid in ("", " ", "x" * 129):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                share.register(invalid, handler)
        with self.assertRaises(TypeError):
            share.register(1, handler)  # type: ignore[arg-type]
        for invalid_handler in (None, 1, object()):
            with (
                self.subTest(invalid_handler=invalid_handler),
                self.assertRaises(TypeError),
            ):
                share.register("other", invalid_handler)  # type: ignore[arg-type]

        class CallableHandler:
            def __call__(self, _peer: NodeId, _params: dict[str, object]) -> str:
                return "ok"

        share.register("callable", CallableHandler())
        share.allow(NodeId("peer"), "callable")
        self.assertEqual(share.request(NodeId("peer"), "callable"), "ok")

    def test_peer_validation_and_revoke_peer_clear_all_capabilities(self) -> None:
        share = CapabilityShare()
        share.register("one", lambda _peer, _params: 1)
        share.register("two", lambda _peer, _params: 2)
        peer = NodeId("peer")
        other = NodeId("other")
        share.allow(peer, "one")
        share.allow(peer, "two")
        share.allow(other, "one")

        for operation in (
            lambda: share.allow("peer", "one"),  # type: ignore[arg-type]
            lambda: share.revoke("peer", "one"),  # type: ignore[arg-type]
            lambda: share.revoke_peer("peer"),  # type: ignore[arg-type]
            lambda: share.request("peer", "one"),  # type: ignore[arg-type]
        ):
            with self.assertRaises(TypeError):
                operation()  # type: ignore[call-arg]

        share.revoke_peer(peer)
        with self.assertRaises(PermissionError):
            share.request(peer, "one")
        with self.assertRaises(PermissionError):
            share.request(peer, "two")
        self.assertEqual(share.request(other, "one"), 1)

        share.clear_grants()
        with self.assertRaises(PermissionError):
            share.request(other, "one")
        share.allow(other, "one")
        self.assertEqual(share.request(other, "one"), 1)

    def test_allow_and_revoke_are_idempotent_and_preserve_other_grants(self) -> None:
        share = CapabilityShare()
        peer = NodeId("peer")
        share.register("one", lambda _peer, _params: 1)
        share.register("two", lambda _peer, _params: 2)
        with self.assertRaises(ValueError):
            share.allow(peer, "missing")
        share.allow(peer, "one")
        share.allow(peer, "one")
        share.allow(peer, "two")
        share.revoke(peer, "one")
        share.revoke(peer, "one")
        self.assertEqual(share.request(peer, "two"), 2)
        with self.assertRaises(PermissionError):
            share.request(peer, "one")

    def test_invalid_capability_ids_are_rejected_at_request(self) -> None:
        share = CapabilityShare()
        share.register("read", lambda _peer, _params: None)
        peer = NodeId("peer")
        for capability in ("", " ", "x" * 129, 1):
            with (
                self.subTest(capability=capability),
                self.assertRaises((TypeError, ValueError)),
            ):
                share.request(peer, capability)  # type: ignore[arg-type]

    def test_reentrant_handler_can_change_grants_without_deadlock(self) -> None:
        share = CapabilityShare()
        peer = NodeId("peer")
        changed = False

        def handler(_peer: NodeId, _params: dict[str, object]) -> str:
            nonlocal changed
            if not changed:
                changed = True
                share.revoke(peer, "read")
                share.allow(peer, "read")
            return "ok"

        share.register("read", handler)
        share.allow(peer, "read")
        with self.assertRaises(PermissionError):
            share.request(peer, "read")
        self.assertEqual(share.request(peer, "read"), "ok")

    def test_revoke_during_request_suppresses_result(self) -> None:
        started = Event()
        release = Event()
        share = CapabilityShare()
        peer = NodeId("peer")

        def handler(_peer: NodeId, _params: dict[str, object]) -> str:
            started.set()
            self.assertTrue(release.wait(1.0))
            return "stale"

        share.register("read", handler)
        share.allow(peer, "read")
        errors: list[BaseException] = []

        def request() -> None:
            try:
                share.request(peer, "read")
            except BaseException as error:  # noqa: BLE001 - test capture
                errors.append(error)

        thread = Thread(target=request)
        thread.start()
        self.assertTrue(started.wait(1.0))
        share.revoke(peer, "read")
        release.set()
        thread.join(1.0)
        self.assertFalse(thread.is_alive())
        self.assertEqual([type(error) for error in errors], [PermissionError])

    def test_unrelated_revocation_does_not_suppress_request_result(self) -> None:
        started = Event()
        release = Event()
        share = CapabilityShare()
        peer = NodeId("peer")
        other = NodeId("other")

        def handler(_peer: NodeId, _params: dict[str, object]) -> str:
            started.set()
            self.assertTrue(release.wait(1.0))
            return "ok"

        share.register("read", handler)
        share.register("other", lambda _peer, _params: None)
        share.allow(peer, "read")
        share.allow(other, "read")
        share.allow(peer, "other")
        results: list[object] = []
        thread = Thread(target=lambda: results.append(share.request(peer, "read")))
        thread.start()
        self.assertTrue(started.wait(1.0))
        share.revoke(other, "read")
        share.revoke(peer, "other")
        release.set()
        thread.join(1.0)
        self.assertFalse(thread.is_alive())
        self.assertEqual(results, ["ok"])

    def test_revoke_and_reallow_during_request_fails_generation_check(self) -> None:
        started = Event()
        release = Event()
        share = CapabilityShare()
        peer = NodeId("peer")

        def handler(_peer: NodeId, _params: dict[str, object]) -> str:
            started.set()
            self.assertTrue(release.wait(1.0))
            return "stale"

        share.register("read", handler)
        share.allow(peer, "read")
        errors: list[BaseException] = []

        def request() -> None:
            try:
                share.request(peer, "read")
            except BaseException as error:  # noqa: BLE001 - test capture
                errors.append(error)

        thread = Thread(target=request)
        thread.start()
        self.assertTrue(started.wait(1.0))
        share.revoke(peer, "read")
        share.allow(peer, "read")
        release.set()
        thread.join(1.0)
        self.assertFalse(thread.is_alive())
        self.assertEqual([type(error) for error in errors], [PermissionError])

    def test_clear_grants_during_request_suppresses_result(self) -> None:
        started = Event()
        release = Event()
        share = CapabilityShare()
        peer = NodeId("peer")

        def handler(_peer: NodeId, _params: dict[str, object]) -> str:
            started.set()
            self.assertTrue(release.wait(1.0))
            return "stale"

        share.register("read", handler)
        share.allow(peer, "read")
        errors: list[BaseException] = []

        def request() -> None:
            try:
                share.request(peer, "read")
            except BaseException as error:  # noqa: BLE001 - test capture
                errors.append(error)

        thread = Thread(target=request)
        thread.start()
        self.assertTrue(started.wait(1.0))
        share.clear_grants()
        release.set()
        thread.join(1.0)
        self.assertFalse(thread.is_alive())
        self.assertEqual([type(error) for error in errors], [PermissionError])

    def test_concurrent_handlers_run_without_registry_lock(self) -> None:
        barrier = Barrier(2)
        share = CapabilityShare()
        peer = NodeId("peer")

        def handler(_peer: NodeId, _params: dict[str, object]) -> str:
            barrier.wait(1.0)
            return "ok"

        share.register("read", handler)
        share.allow(peer, "read")
        results: list[object] = []
        threads = [
            Thread(target=lambda: results.append(share.request(peer, "read")))
            for _ in range(2)
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(1.0)
        self.assertTrue(all(not thread.is_alive() for thread in threads))
        self.assertEqual(results, ["ok", "ok"])

    def test_concurrent_duplicate_registration_has_one_winner(self) -> None:
        barrier = Barrier(2)
        share = CapabilityShare()
        outcomes: list[type[BaseException] | None] = []

        def register() -> None:
            barrier.wait(1.0)
            try:
                share.register("read", lambda _peer, _params: None)
            except BaseException as error:  # noqa: BLE001 - test capture
                outcomes.append(type(error))
            else:
                outcomes.append(None)

        threads = [Thread(target=register), Thread(target=register)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(1.0)
        self.assertTrue(all(not thread.is_alive() for thread in threads))
        self.assertCountEqual(outcomes, [None, ValueError])

    def test_concurrent_allow_and_revoke_leave_registry_consistent(self) -> None:
        share = CapabilityShare()
        peer = NodeId("peer")
        share.register("read", lambda _peer, _params: "ok")
        barrier = Barrier(2)
        errors: list[BaseException] = []

        def allow() -> None:
            try:
                barrier.wait(1.0)
                for _ in range(50):
                    share.allow(peer, "read")
            except BaseException as error:  # noqa: BLE001 - test capture
                errors.append(error)

        def revoke() -> None:
            try:
                barrier.wait(1.0)
                for _ in range(50):
                    share.revoke(peer, "read")
            except BaseException as error:  # noqa: BLE001 - test capture
                errors.append(error)

        threads = [Thread(target=allow), Thread(target=revoke)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(1.0)
        self.assertTrue(all(not thread.is_alive() for thread in threads))
        self.assertEqual(errors, [])
        share.allow(peer, "read")
        self.assertEqual(share.request(peer, "read"), "ok")


class SurfaceRegistryTests(unittest.TestCase):
    def test_surface_and_action_ids_are_opaque_but_strictly_bounded(self) -> None:
        registry = SurfaceRegistry()
        registry.register("nested/surface.v1", read=lambda _peer, _params: "read")
        registry.register(
            "other",
            read=lambda _peer, _params: "other",
            actions={"action.with/slash": lambda _peer, _params: "action"},
        )

        for invalid in ("", " ", "x" * 129):
            with self.assertRaises(ValueError):
                registry.register(invalid, read=lambda _peer, _params: None)
        with self.assertRaises(TypeError):
            registry.register(1, read=lambda _peer, _params: None)  # type: ignore[arg-type]
        with self.assertRaises(ValueError):
            registry.register("nested/surface.v1", read=lambda _peer, _params: None)
        with self.assertRaises(ValueError):
            registry.register(
                "actions",
                read=lambda _peer, _params: None,
                actions={"x" * 129: lambda _peer, _params: None},
            )

    def test_registers_read_review_and_named_action_handlers(self) -> None:
        registry = SurfaceRegistry()
        peer = NodeId("peer")
        registry.register(
            "surface",
            read=lambda caller, params: ("read", caller, params),
            review=lambda caller, params: ("review", caller, params),
            actions={"save": lambda caller, params: ("save", caller, params)},
        )
        registry.grant_peer(peer, "surface", access=SurfaceAccess.READ)
        registry.grant_peer(peer, "surface", access=SurfaceAccess.REVIEW)
        registry.grant_peer(peer, "surface", access=SurfaceAccess.ACTION)

        self.assertEqual(
            registry.dispatch(peer, "surface", access=SurfaceAccess.READ, params={"x": 1}),
            ("read", peer, {"x": 1}),
        )
        self.assertEqual(
            registry.dispatch(peer, "surface", access="review"),
            ("review", peer, {}),
        )
        self.assertEqual(
            registry.dispatch(peer, "surface", access="action", action="save"),
            ("save", peer, {}),
        )

    def test_duplicate_actions_and_surfaces_are_rejected(self) -> None:
        registry = SurfaceRegistry()
        registry.register("surface", read=lambda _peer, _params: None)
        with self.assertRaises(ValueError):
            registry.register("surface", read=lambda _peer, _params: None)
        with self.assertRaises(ValueError):
            registry.register(
                "other",
                read=lambda _peer, _params: None,
                actions=[
                    ("save", lambda _peer, _params: None),
                    ("save", lambda _peer, _params: None),
                ],
            )

    def test_direct_grants_can_be_revoked_or_stopped(self) -> None:
        registry = SurfaceRegistry()
        peer = NodeId("peer")
        registry.register("surface", read=lambda _peer, _params: "ok")
        registry.grant_peer(peer, "surface", access="read")
        self.assertEqual(registry.dispatch(peer, "surface", access="read"), "ok")
        registry.revoke_peer(peer, "surface", access=SurfaceAccess.READ)
        with self.assertRaises(PermissionError):
            registry.dispatch(peer, "surface", access="read")

        registry.grant_peer(peer, "surface", access="read")
        registry.stop_peer(peer, "surface")
        with self.assertRaises(PermissionError):
            registry.dispatch(peer, "surface", access="read")

    def test_grants_expire_and_cluster_sources_are_explicit(self) -> None:
        current_time = [10.0]
        registry = SurfaceRegistry(clock=lambda: current_time[0])
        peer = NodeId("peer")
        registry.register("surface", read=lambda _peer, _params: "ok")
        registry.grant_peer(peer, "surface", access="read", expires_at=10.0)
        self.assertEqual(registry.dispatch(peer, "surface", access="read"), "ok")
        current_time[0] = 11.0
        with self.assertRaises(PermissionError):
            registry.dispatch(peer, "surface", access="read")
        registry.grant_cluster("cluster-grant", "surface", access="read", expires_at=20.0)
        self.assertEqual(
            registry.dispatch(
                peer,
                "surface",
                access="read",
                cluster_sources=("cluster-grant",),
            ),
            "ok",
        )
        with self.assertRaises(PermissionError):
            registry.dispatch(NodeId("other"), "surface", access="read")

    def test_handler_failures_have_no_raw_exception_text(self) -> None:
        registry = SurfaceRegistry()
        peer = NodeId("peer")

        def broken(_peer: NodeId, _params: dict[str, object]) -> object:
            raise RuntimeError("private handler detail")

        registry.register("surface", read=broken)
        registry.grant_peer(peer, "surface", access="read")
        with self.assertRaises(SurfaceHandlerError) as raised:
            registry.dispatch(peer, "surface", access="read")
        self.assertNotIn("private handler detail", str(raised.exception))

    def test_surface_records_are_immutable(self) -> None:
        registry = SurfaceRegistry()
        definition = registry.register("surface", read=lambda _peer, _params: None)
        grant = registry.grant_peer(NodeId("peer"), "surface", access="read")
        with self.assertRaises(FrozenInstanceError):
            definition.surface_id = "changed"  # type: ignore[misc]
        with self.assertRaises(FrozenInstanceError):
            grant.access = SurfaceAccess.ACTION  # type: ignore[misc]

import unittest
from dataclasses import FrozenInstanceError

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

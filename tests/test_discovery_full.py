import unittest
from collections.abc import Callable
from typing import Any

from expra_connect.discovery_full import (
    EVENT_CANDIDATE,
    EVENT_LOST,
    SERVICE_TYPE,
    DiscoveryAdvertisement,
    NetworkDiscovery,
)


class _Info:
    port = 27321

    def __init__(
        self,
        node_id: str,
        address: bytes = b"192.168.1.10",
        fingerprint: bytes | None = None,
    ) -> None:
        self.addresses = [address]
        self.properties = {
            b"id": node_id.encode(),
            b"name": b"Peer B",
            b"app_version": b"1.0",
            b"protocol_version": b"1",
            b"connectable": b"true",
        }
        if fingerprint is not None:
            self.properties[b"tls_fingerprint"] = fingerprint


class _Backend:
    available = True

    def __init__(self, listener: Callable[..., None]) -> None:
        self.listener = listener
        self.started = False

    def start(self, advertisement: Any) -> None:
        self.started = True

    def stop(self) -> None:
        self.started = False


class FullDiscoveryTests(unittest.TestCase):
    def test_custom_service_type_is_used_for_peer_matching(self) -> None:
        holder: dict[str, _Backend] = {}

        def factory(listener: Callable[..., None]) -> _Backend:
            backend = _Backend(listener)
            holder["backend"] = backend
            return backend

        discovery = NetworkDiscovery(
            "local-node",
            advertisement=DiscoveryAdvertisement(
                stable_id="local-node",
                display_name="Local",
                hostname="localhost",
                app_version="1.0",
            ),
            backend_factory=factory,
            service_type="_custom._tcp.local.",
        )
        self.assertTrue(discovery.start())
        holder["backend"].listener("add", "peer-b._custom._tcp.local.", _Info("peer-b"))
        holder["backend"].listener("add", f"ignored.{SERVICE_TYPE}", _Info("ignored"))

        self.assertEqual([item.stable_id for item in discovery.peers()], ["peer-b"])

    def test_lifecycle_normalizes_events_filters_self_and_expires(self) -> None:
        now = [100.0]
        events: list[tuple[str, Any]] = []
        holder: dict[str, _Backend] = {}

        def factory(listener: Callable[..., None]) -> _Backend:
            backend = _Backend(listener)
            holder["backend"] = backend
            return backend

        discovery = NetworkDiscovery(
            "local-node",
            advertisement=DiscoveryAdvertisement(
                stable_id="local-node",
                display_name="Local",
                hostname="localhost",
                app_version="1.0",
            ),
            backend_factory=factory,
            clock=lambda: now[0],
            ttl_seconds=5.0,
            on_event=lambda kind, payload: events.append((kind, payload)),
        )
        self.assertTrue(discovery.start())
        holder["backend"].listener("add", f"peer-b.{SERVICE_TYPE}", _Info("peer-b"))
        holder["backend"].listener(
            "add", f"local-node.{SERVICE_TYPE}", _Info("local-node")
        )
        self.assertEqual([item.stable_id for item in discovery.peers()], ["peer-b"])
        self.assertEqual(events[0][0], EVENT_CANDIDATE)
        now[0] = 106.0
        discovery.expire_stale()
        self.assertEqual(discovery.peers(), ())
        self.assertEqual(events[-1][0], EVENT_LOST)
        discovery.stop()
        self.assertFalse(discovery.active)

    def test_duplicate_service_announcements_merge_routes_and_remove_independently(
        self,
    ) -> None:
        holder: dict[str, _Backend] = {}

        def factory(listener: Callable[..., None]) -> _Backend:
            backend = _Backend(listener)
            holder["backend"] = backend
            return backend

        discovery = NetworkDiscovery(
            "local-node",
            advertisement=DiscoveryAdvertisement(
                stable_id="local-node",
                display_name="Local",
                hostname="localhost",
                app_version="1",
            ),
            backend_factory=factory,
        )
        self.assertTrue(discovery.start())
        backend = holder["backend"]
        backend.listener(
            "add", f"peer-a.{SERVICE_TYPE}", _Info("peer", b"\xc0\xa8\x01\n")
        )
        backend.listener(
            "add", f"peer-b.{SERVICE_TYPE}", _Info("peer", b"\xc0\xa8\x01\x0b")
        )
        self.assertEqual(
            discovery.peers()[0].addresses, ("192.168.1.10", "192.168.1.11")
        )
        backend.listener("remove", f"peer-a.{SERVICE_TYPE}", None)
        self.assertEqual(len(discovery.peers()), 1)

    def test_conflicting_transport_fingerprint_does_not_replace_existing_peer(
        self,
    ) -> None:
        holder: dict[str, _Backend] = {}

        def factory(listener: Callable[..., None]) -> _Backend:
            backend = _Backend(listener)
            holder["backend"] = backend
            return backend

        discovery = NetworkDiscovery(
            "local-node",
            advertisement=DiscoveryAdvertisement(
                stable_id="local-node",
                display_name="Local",
                hostname="localhost",
                app_version="1",
            ),
            backend_factory=factory,
        )
        self.assertTrue(discovery.start())
        backend = holder["backend"]
        backend.listener(
            "add", f"peer-a.{SERVICE_TYPE}", _Info("peer", fingerprint=b"one")
        )
        backend.listener(
            "update", f"peer-a.{SERVICE_TYPE}", _Info("peer", fingerprint=b"two")
        )
        self.assertEqual(discovery.peers()[0].transport_fingerprint, "one")

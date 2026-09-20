import unittest
from collections.abc import Callable
from threading import Event, Thread
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

from expra_connect.discovery_full import (
    EVENT_CANDIDATE,
    EVENT_LOST,
    SERVICE_TYPE,
    DiscoveryAdvertisement,
    NetworkDiscovery,
    _service_addresses,
    _service_instance_id,
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
        self.parsed_addresses: Callable[[], list[str]] | None = None
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


class _SynchronousBackend(_Backend):
    def start(self, advertisement: Any) -> None:
        super().start(advertisement)
        self.listener(
            "add",
            "peer._expra-peer._tcp.local.",
            _Info("peer"),
        )


class FullDiscoveryTests(unittest.TestCase):
    def test_rotated_transport_gets_a_new_service_instance_name(self) -> None:
        base = DiscoveryAdvertisement(
            stable_id="peer-a",
            display_name="Peer",
            hostname="peer-a",
            app_version="1",
            transport_generation=1,
        )
        rotated = DiscoveryAdvertisement(
            stable_id="peer-a",
            display_name="Peer",
            hostname="peer-a",
            app_version="1",
            transport_generation=2,
        )
        self.assertEqual(_service_instance_id(base), "peer-a")
        self.assertEqual(_service_instance_id(rotated), "peer-a-g2")

    def test_explicit_advertised_addresses_exclude_unconfigured_interfaces(self) -> None:
        addresses = _service_addresses(("192.168.1.20", "2001:db8::20"))
        self.assertEqual(
            {address for address in addresses},
            {b"\xc0\xa8\x01\x14", bytes.fromhex("20010db8000000000000000000000020")},
        )

    def test_zeroconf_start_does_not_require_optional_address_helpers(self) -> None:
        registered: list[Any] = []

        class FakeZeroconf:
            def __init__(self, **_kwargs: Any) -> None:
                pass

            def register_service(self, service_info: Any) -> None:
                registered.append(service_info)

            def close(self) -> None:
                pass

        class FakeServiceInfo:
            def __init__(self, *_args: Any, **kwargs: Any) -> None:
                self.kwargs = kwargs

        class FakeServiceBrowser:
            def __init__(self, *_args: Any) -> None:
                pass

            def cancel(self) -> None:
                pass

        fake_module = SimpleNamespace(
            Zeroconf=FakeZeroconf,
            ServiceInfo=FakeServiceInfo,
            ServiceBrowser=FakeServiceBrowser,
        )
        with patch("expra_connect.discovery_full._zeroconf_module", fake_module):
            from expra_connect.discovery_full import ZeroconfDiscoveryBackend

            backend = ZeroconfDiscoveryBackend(lambda *_args: None)
            backend.start(
                DiscoveryAdvertisement(
                    stable_id="local",
                    display_name="Local",
                    hostname="localhost",
                    app_version="1",
                    advertised_addresses=("192.168.1.20",),
                )
            )
            backend.stop()

        self.assertEqual(len(registered), 1)

    def test_zeroconf_retries_ipv4_when_all_interfaces_fail(self) -> None:
        created: list[dict[str, Any]] = []

        class FakeZeroconf:
            def __init__(self, **kwargs: Any) -> None:
                created.append(kwargs)
                if kwargs.get("ip_version") == "all":
                    raise OSError("IPv6 unavailable")

            def register_service(self, _service_info: Any) -> None:
                pass

            def close(self) -> None:
                pass

        class FakeServiceInfo:
            def __init__(self, *_args: Any, **_kwargs: Any) -> None:
                pass

        class FakeServiceBrowser:
            def __init__(self, *_args: Any) -> None:
                pass

            def cancel(self) -> None:
                pass

        fake_module = SimpleNamespace(
            Zeroconf=FakeZeroconf,
            ServiceInfo=FakeServiceInfo,
            ServiceBrowser=FakeServiceBrowser,
            IPVersion=SimpleNamespace(All="all", V4Only="v4"),
        )
        with patch("expra_connect.discovery_full._zeroconf_module", fake_module):
            from expra_connect.discovery_full import ZeroconfDiscoveryBackend

            backend = ZeroconfDiscoveryBackend(lambda *_args: None)
            backend.start(
                DiscoveryAdvertisement(
                    stable_id="local",
                    display_name="Local",
                    hostname="localhost",
                    app_version="1",
                )
            )
            backend.stop()

        self.assertEqual(created, [{"ip_version": "all"}, {"ip_version": "v4"}])

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

    def test_synchronous_start_callback_is_retained(self) -> None:
        discovery = NetworkDiscovery(
            "local-node",
            advertisement=DiscoveryAdvertisement(
                stable_id="local-node",
                display_name="Local",
                hostname="localhost",
                app_version="1.0",
            ),
            backend_factory=lambda listener: _SynchronousBackend(listener),
        )

        self.assertTrue(discovery.start())
        self.assertEqual([item.stable_id for item in discovery.peers()], ["peer"])

    def test_start_and_stop_are_serialized_with_blocking_backend(self) -> None:
        started = Event()
        release = Event()

        class BlockingBackend(_Backend):
            def start(self, advertisement: Any) -> None:
                started.set()
                release.wait(1.0)
                super().start(advertisement)

        backend = BlockingBackend(lambda *_args: None)
        discovery = NetworkDiscovery(
            "local-node",
            advertisement=DiscoveryAdvertisement(
                stable_id="local-node",
                display_name="Local",
                hostname="localhost",
                app_version="1.0",
            ),
            backend_factory=lambda _listener: backend,
        )
        start_thread = Thread(target=discovery.start)
        start_thread.start()
        self.assertTrue(started.wait(1.0))
        stop_thread = Thread(target=discovery.stop)
        stop_thread.start()
        release.set()
        start_thread.join(1.0)
        stop_thread.join(1.0)

        self.assertFalse(discovery.active)
        self.assertFalse(backend.started)

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

    def test_same_service_update_replaces_stale_route(self) -> None:
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
            "add", f"peer.{SERVICE_TYPE}", _Info("peer", b"\xc0\xa8\x01\n")
        )
        backend.listener(
            "update", f"peer.{SERVICE_TYPE}", _Info("peer", b"\xc0\xa8\x01\x14")
        )

        self.assertEqual(discovery.peers()[0].addresses, ("192.168.1.20",))

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
        self.assertEqual(discovery.peers()[0].transport_fingerprint, "two")

    def test_invalid_port_zero_is_non_connectable_not_an_exception(self) -> None:
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
        holder["backend"].listener("add", f"peer.{SERVICE_TYPE}", _Info("peer"))
        info = _Info("zero")
        info.port = 0
        holder["backend"].listener("add", f"zero.{SERVICE_TYPE}", info)

        peer = next(item for item in discovery.peers() if item.stable_id == "zero")
        self.assertIsNone(peer.port)
        self.assertEqual(peer.endpoint_candidates, ())

    def test_same_hostname_different_node_id_is_not_self(self) -> None:
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
                hostname="same-host",
                app_version="1",
            ),
            backend_factory=factory,
        )
        self.assertTrue(discovery.start())
        info = _Info("remote")
        info.properties[b"name"] = b"same-host"
        holder["backend"].listener("add", f"remote.{SERVICE_TYPE}", info)
        self.assertEqual([item.stable_id for item in discovery.peers()], ["remote"])

    def test_bytes_properties_and_empty_addresses_are_safe(self) -> None:
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
        info = _Info("ipv6")
        info.addresses = []
        info.parsed_addresses = lambda: ["2001:db8::9"]
        holder["backend"].listener("add", f"ipv6.{SERVICE_TYPE}", info)

        self.assertEqual(discovery.peers()[0].addresses, ("2001:db8::9",))

    def test_late_event_after_stop_cannot_repopulate_candidates(self) -> None:
        holder: dict[str, _Backend] = {}
        events: list[tuple[str, Any]] = []

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
            on_event=lambda kind, payload: events.append((kind, payload)),
        )
        self.assertTrue(discovery.start())
        discovery.stop()
        holder["backend"].listener("add", f"late.{SERVICE_TYPE}", _Info("late"))

        self.assertEqual(discovery.peers(), ())
        self.assertEqual(events, [])

    def test_default_ttl_exceeds_mdns_record_ttl(self) -> None:
        from expra_connect.discovery_full import DEFAULT_TTL_SECONDS

        self.assertGreater(DEFAULT_TTL_SECONDS, 4500.0)

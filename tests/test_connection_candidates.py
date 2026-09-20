import unittest
from dataclasses import replace
from threading import Thread
from unittest.mock import patch

from expra_connect.connection_manager import ConnectionManager
from expra_connect.connection_state import ConnectionStatus
from expra_connect.discovery import (
    DiscoveryCandidate,
    DiscoveryRegistry,
    preferred_endpoint,
)
from expra_connect.identity import NodeId, NodeIdentity
from expra_connect.models import (
    DiscoveredNodeCandidate,
    EndpointCandidate,
    EndpointSource,
)
from expra_connect.pairing import PairingManager, TrustedPeer
from expra_connect.registry import NodeRegistry
from expra_connect.wire_protocol import (
    RemoteAuthError,
    RemoteProtocolError,
    RemoteTransportError,
)


def candidate(*endpoints: EndpointCandidate) -> DiscoveredNodeCandidate:
    return DiscoveredNodeCandidate(
        stable_id="peer-node",
        hostname="peer.local",
        addresses=tuple(endpoint.address for endpoint in endpoints),
        port=endpoints[0].port if endpoints else None,
        service_name="peer._expra-peer._tcp.local.",
        app_version="1",
        protocol_version="1",
        platform=None,
        connectable=True,
        compatible=True,
        last_seen=1.0,
        transport_fingerprint="fingerprint",
        endpoint_candidates=endpoints,
    )


class SuccessfulProvider:
    def __init__(self, identity: NodeIdentity) -> None:
        self._identity = identity

    def hello(self) -> dict[str, object]:
        return {
            "capabilities": [],
            "transport_fingerprint": "replacement-fingerprint",
            "root_public_key": self._identity.root_public_key,
            "transport_generation": 2,
            "transport_proof": self._identity.sign_transport_proof(
                2, "replacement-fingerprint"
            ),
        }


class CandidateTests(unittest.TestCase):
    def test_legacy_endpoint_fields_are_adapted(self) -> None:
        item = candidate(EndpointCandidate("192.168.1.2", 27321))
        self.assertEqual(item.endpoint_candidates[0].address, "192.168.1.2")
        self.assertEqual(item.addresses, ("192.168.1.2",))

    def test_route_ranking_is_deterministic_and_prefers_configured_endpoint(
        self,
    ) -> None:
        endpoints = (
            EndpointCandidate("10.0.0.2", 27321, source=EndpointSource.VPN),
            EndpointCandidate("2001:db8::2", 27321, source=EndpointSource.IPV6),
            EndpointCandidate("192.168.1.2", 27321, source=EndpointSource.IPV4),
            EndpointCandidate(
                "configured.example", 27321, source=EndpointSource.CONFIGURED
            ),
        )
        self.assertEqual(preferred_endpoint(endpoints).address, "configured.example")

    def test_legacy_discovery_registry_replaces_duplicate_observation(self) -> None:
        registry = DiscoveryRegistry(clock=lambda: 10.0)
        self.assertTrue(
            registry.add(DiscoveryCandidate("peer", ("192.168.1.2",), 27321))
        )
        self.assertTrue(
            registry.add(DiscoveryCandidate("peer", ("2001:db8::2",), 27321))
        )
        item = registry.candidates()[0]
        self.assertEqual(item.addresses, ("2001:db8::2",))


class ConnectionManagerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.peer = NodeId("peer-node")
        self.pairing = PairingManager(NodeId("local-node"))
        self.pairing.trusted[self.peer] = TrustedPeer(
            self.peer,
            "a" * 64,
            frozenset({"read_state"}),
            transport_fingerprint="fingerprint",
        )
        self.registry = NodeRegistry(NodeId("local-node"))
        self.candidates = {
            self.peer.value: candidate(
                EndpointCandidate("192.168.1.2", 27321, source=EndpointSource.IPV4),
                EndpointCandidate("10.8.0.2", 27321, source=EndpointSource.VPN),
            )
        }

    def test_route_failure_falls_back_without_changing_peer_identity(self) -> None:
        attempts: list[str] = []
        route_events: list[tuple[str, str, str, str | None]] = []

        class Provider:
            def __init__(self, address: str) -> None:
                self.address = address

            def hello(self) -> dict[str, object]:
                attempts.append(self.address)
                if self.address == "192.168.1.2":
                    raise RemoteTransportError("route failed")
                return {"capabilities": []}

        manager = ConnectionManager(
            local_id=NodeId("local-node"),
            pairing=self.pairing,
            registry=self.registry,
            candidates=self.candidates,
            provider_factory=lambda **kwargs: Provider(kwargs["transport"].address),
            on_route_attempt=lambda phase, endpoint, outcome, error: (
                route_events.append((phase, endpoint.address, outcome, error))
            ),
        )
        with patch(
            "expra_connect.connection_manager.TLSRemoteTransport",
            side_effect=lambda host, port, **_: type("T", (), {"address": host})(),
        ):
            manager.connect(self.peer)
        self.assertEqual(attempts, ["192.168.1.2", "10.8.0.2"])
        self.assertEqual(
            [(item[0], item[1], item[2]) for item in route_events],
            [
                ("connection", "192.168.1.2", "started"),
                ("connection", "192.168.1.2", "failed"),
                ("connection", "10.8.0.2", "started"),
                ("connection", "10.8.0.2", "succeeded"),
            ],
        )
        record = self.registry.record(self.peer)
        assert record is not None
        self.assertEqual(record.connection.status, ConnectionStatus.ONLINE)
        self.assertEqual(manager.connection_generation(self.peer), 1)

    def test_authentication_failure_does_not_try_another_route(self) -> None:
        attempts: list[str] = []

        class Provider:
            def hello(self) -> dict[str, object]:
                attempts.append("attempt")
                raise RemoteAuthError("fingerprint mismatch")

        manager = ConnectionManager(
            local_id=NodeId("local-node"),
            pairing=self.pairing,
            registry=self.registry,
            candidates=self.candidates,
            provider_factory=lambda **_: Provider(),
        )
        with (
            patch("expra_connect.connection_manager.TLSRemoteTransport"),
            self.assertRaises(RemoteAuthError),
        ):
            manager.connect(self.peer)
        self.assertEqual(attempts, ["attempt"])
        record = self.registry.record(self.peer)
        assert record is not None
        self.assertEqual(
            record.connection.status,
            ConnectionStatus.AUTHENTICATION_FAILED,
        )

    def test_stale_generation_cannot_commit_after_disconnect(self) -> None:
        manager = ConnectionManager(
            local_id=NodeId("local-node"),
            pairing=self.pairing,
            registry=self.registry,
            candidates=self.candidates,
        )
        generation = manager.connection_generation(self.peer)
        manager.disconnect(self.peer)
        self.assertFalse(manager.is_current_generation(self.peer, generation))

    def test_repeated_connect_deduplicates_the_live_provider(self) -> None:
        class Provider:
            def hello(self) -> dict[str, object]:
                return {"capabilities": []}

        manager = ConnectionManager(
            local_id=NodeId("local-node"),
            pairing=self.pairing,
            registry=self.registry,
            candidates=self.candidates,
            provider_factory=lambda **_: Provider(),
        )
        with patch("expra_connect.connection_manager.TLSRemoteTransport"):
            first = manager.connect(self.peer)
            second = manager.connect(self.peer)
        self.assertIs(first, second)
        self.assertEqual(manager.connection_generation(self.peer), 1)

    def test_reconnect_replaces_the_live_provider_and_advances_generation(self) -> None:
        providers: list[object] = []

        class Provider:
            def hello(self) -> dict[str, object]:
                return {"capabilities": []}

        def build_provider(**_: object) -> Provider:
            provider = Provider()
            providers.append(provider)
            return provider

        manager = ConnectionManager(
            local_id=NodeId("local-node"),
            pairing=self.pairing,
            registry=self.registry,
            candidates=self.candidates,
            provider_factory=build_provider,
        )
        with patch("expra_connect.connection_manager.TLSRemoteTransport"):
            first = manager.connect(self.peer)
            second = manager.reconnect(self.peer)
        self.assertIsNot(first, second)
        self.assertEqual(len(providers), 2)
        self.assertEqual(manager.connection_generation(self.peer), 2)

    def test_simultaneous_connects_are_deduplicated(self) -> None:
        attempts = 0

        class Provider:
            def hello(self) -> dict[str, object]:
                return {"capabilities": []}

        def build_provider(**_: object) -> Provider:
            nonlocal attempts
            attempts += 1
            return Provider()

        manager = ConnectionManager(
            local_id=NodeId("local-node"),
            pairing=self.pairing,
            registry=self.registry,
            candidates=self.candidates,
            provider_factory=build_provider,
        )
        results: list[object] = []
        with patch("expra_connect.connection_manager.TLSRemoteTransport"):
            threads = [
                Thread(target=lambda: results.append(manager.connect(self.peer)))
                for _ in range(4)
            ]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join()
        self.assertEqual(attempts, 1)
        self.assertEqual(len(results), 4)

    def test_all_routes_failed_marks_peer_offline(self) -> None:
        manager = ConnectionManager(
            local_id=NodeId("local-node"),
            pairing=self.pairing,
            registry=self.registry,
            candidates=self.candidates,
        )
        with (
            patch(
                "expra_connect.connection_manager.TLSRemoteTransport",
                side_effect=RemoteTransportError("route failed"),
            ),
            self.assertRaises(RemoteTransportError),
        ):
            manager.connect(self.peer)
        record = self.registry.record(self.peer)
        assert record is not None
        self.assertEqual(
            record.connection.status,
            ConnectionStatus.OFFLINE,
        )

    def test_malformed_hello_marks_authentication_failed(self) -> None:
        class Provider:
            def hello(self) -> dict[str, object]:
                return {"capabilities": "not-a-list"}

        manager = ConnectionManager(
            local_id=NodeId("local-node"),
            pairing=self.pairing,
            registry=self.registry,
            candidates=self.candidates,
            provider_factory=lambda **_: Provider(),
        )
        with (
            patch("expra_connect.connection_manager.TLSRemoteTransport"),
            self.assertRaises(RemoteProtocolError),
        ):
            manager.connect(self.peer)
        record = self.registry.record(self.peer)
        assert record is not None
        self.assertEqual(
            record.connection.status,
            ConnectionStatus.AUTHENTICATION_FAILED,
        )

    def test_malformed_hello_reports_a_failed_route(self) -> None:
        events: list[tuple[str, str]] = []

        class Provider:
            def hello(self) -> dict[str, object]:
                return {"capabilities": None}

        manager = ConnectionManager(
            local_id=NodeId("local-node"),
            pairing=self.pairing,
            registry=self.registry,
            candidates=self.candidates,
            provider_factory=lambda **_: Provider(),
            on_route_attempt=lambda _phase, endpoint, outcome, _error: events.append(
                (endpoint.address, outcome)
            ),
        )
        with (
            patch("expra_connect.connection_manager.TLSRemoteTransport"),
            self.assertRaises(RemoteProtocolError),
        ):
            manager.connect(self.peer)
        self.assertEqual(
            events,
            [("192.168.1.2", "started"), ("192.168.1.2", "failed")],
        )

    def _replacement_candidate(self) -> tuple[NodeIdentity, DiscoveredNodeCandidate]:
        identity = NodeIdentity.create(self.peer)
        self.pairing.trusted[self.peer] = replace(
            self.pairing.trusted[self.peer],
            root_public_key=identity.root_public_key,
        )
        candidate = replace(
            self.candidates[self.peer.value],
            transport_fingerprint="replacement-fingerprint",
            root_public_key=identity.root_public_key,
            transport_generation=2,
            transport_proof=identity.sign_transport_proof(
                2, "replacement-fingerprint"
            ),
        )
        self.candidates[self.peer.value] = candidate
        return identity, candidate

    def test_generation_persistence_failure_marks_authentication_failed(self) -> None:
        identity, _ = self._replacement_candidate()
        manager = ConnectionManager(
            local_id=NodeId("local-node"),
            pairing=self.pairing,
            registry=self.registry,
            candidates=self.candidates,
            provider_factory=lambda **_: SuccessfulProvider(identity),
            persist=lambda: False,
        )
        with (
            patch("expra_connect.connection_manager.TLSRemoteTransport"),
            self.assertRaises(RemoteAuthError),
        ):
            manager.connect(self.peer)
        record = self.registry.record(self.peer)
        assert record is not None
        self.assertEqual(
            record.connection.status,
            ConnectionStatus.AUTHENTICATION_FAILED,
        )

    def test_generation_persistence_failure_does_not_promote_capabilities(self) -> None:
        identity, _ = self._replacement_candidate()
        manager = ConnectionManager(
            local_id=NodeId("local-node"),
            pairing=self.pairing,
            registry=self.registry,
            candidates=self.candidates,
            provider_factory=lambda **_: SuccessfulProvider(identity),
            persist=lambda: False,
        )
        with (
            patch("expra_connect.connection_manager.TLSRemoteTransport"),
            self.assertRaises(RemoteAuthError),
        ):
            manager.connect(self.peer)
        record = self.registry.record(self.peer)
        assert record is not None
        self.assertEqual(record.capabilities, frozenset())

    def test_generation_persistence_failure_does_not_retain_provider(self) -> None:
        identity, _ = self._replacement_candidate()

        manager = ConnectionManager(
            local_id=NodeId("local-node"),
            pairing=self.pairing,
            registry=self.registry,
            candidates=self.candidates,
            provider_factory=lambda **_: SuccessfulProvider(identity),
            persist=lambda: False,
        )
        with (
            patch("expra_connect.connection_manager.TLSRemoteTransport"),
            self.assertRaises(RemoteAuthError),
        ):
            manager.connect(self.peer)
        self.assertEqual(manager.providers, ())

    def test_generation_persistence_failure_reports_a_failed_route(self) -> None:
        identity, _ = self._replacement_candidate()
        events: list[str] = []
        manager = ConnectionManager(
            local_id=NodeId("local-node"),
            pairing=self.pairing,
            registry=self.registry,
            candidates=self.candidates,
            provider_factory=lambda **_: SuccessfulProvider(identity),
            persist=lambda: False,
            on_route_attempt=lambda _phase, _endpoint, outcome, _error: events.append(
                outcome
            ),
        )
        with (
            patch("expra_connect.connection_manager.TLSRemoteTransport"),
            self.assertRaises(RemoteAuthError),
        ):
            manager.connect(self.peer)
        self.assertEqual(events, ["started", "failed"])

    def test_disconnect_all_uses_a_stable_provider_snapshot(self) -> None:
        manager = ConnectionManager(
            local_id=NodeId("local-node"),
            pairing=self.pairing,
            registry=self.registry,
            candidates=self.candidates,
        )
        manager._providers[self.peer] = object()
        disconnected: list[NodeId] = []

        def disconnect(peer_id: NodeId) -> None:
            disconnected.append(peer_id)
            manager._providers[NodeId("another-peer")] = object()

        manager.disconnect = disconnect  # type: ignore[method-assign]
        manager.disconnect_all()
        self.assertEqual(disconnected, [self.peer])

import unittest
from types import SimpleNamespace

from expra_connect.discovery import (
    DiscoveryCandidate,
    DiscoveryRegistry,
    normalize_port,
    preferred_address,
    validate_candidate,
)
from expra_connect.discovery_backend import ZeroconfBackend


class DiscoveryTests(unittest.TestCase):
    def test_candidate_validation_rejects_self_and_bad_ports(self) -> None:
        with self.assertRaises(ValueError):
            validate_candidate(DiscoveryCandidate("local", ("192.168.1.2",), 27321))
        with self.assertRaises(ValueError):
            validate_candidate(DiscoveryCandidate("peer", ("192.168.1.2",), 0))

    def test_candidate_validation_rejects_non_integer_ports(self) -> None:
        with self.assertRaises(ValueError):
            validate_candidate(
                DiscoveryCandidate("peer", ("192.168.1.2",), True)  # type: ignore[arg-type]
            )
        with self.assertRaises(ValueError):
            validate_candidate(
                DiscoveryCandidate("peer", ("192.168.1.2",), 1.5)  # type: ignore[arg-type]
            )

    def test_private_route_is_preferred_without_being_identity(self) -> None:
        self.assertEqual(
            preferred_address(("100.64.1.2", "192.168.1.2")), "192.168.1.2"
        )

    def test_vpn_route_is_used_when_it_is_the_only_candidate(self) -> None:
        self.assertEqual(preferred_address(("100.64.1.2",)), "100.64.1.2")

    def test_172_31_is_private_and_malformed_routes_do_not_crash(self) -> None:
        self.assertEqual(preferred_address(("bad", "172.31.1.2")), "172.31.1.2")

    def test_ipv6_only_route_is_preserved(self) -> None:
        self.assertEqual(preferred_address(("2001:db8::1",)), "2001:db8::1")

    def test_equal_ranked_routes_keep_advertisement_order(self) -> None:
        addresses = ("192.168.1.20", "192.168.1.10")
        self.assertEqual(preferred_address(addresses), addresses[0])

    def test_registry_expires_candidates_and_deduplicates_ids(self) -> None:
        registry = DiscoveryRegistry(clock=lambda: 10.0, ttl=5.0)
        registry.add(DiscoveryCandidate("peer", ("10.0.0.1",), 27321))
        registry.add(DiscoveryCandidate("peer", ("10.0.0.2",), 27321))
        self.assertEqual(registry.candidates()[0].addresses, ("10.0.0.2",))
        self.assertEqual(registry.expire(now=16.0), ("peer",))

    def test_registry_ignores_self_and_malformed_events(self) -> None:
        registry = DiscoveryRegistry(self_id="local")
        self.assertFalse(
            registry.add(DiscoveryCandidate("local", ("127.0.0.1",), 27321))
        )
        self.assertFalse(registry.add(DiscoveryCandidate("peer", (), 27321)))

    def test_port_normalization_rejects_boolean_fractional_and_out_of_range(
        self,
    ) -> None:
        self.assertEqual(normalize_port(27321), 27321)
        self.assertIsNone(normalize_port(True))
        self.assertIsNone(normalize_port(27321.5))
        self.assertIsNone(normalize_port(70000))

    def test_port_normalization_rejects_non_finite_numbers(self) -> None:
        self.assertIsNone(normalize_port(float("nan")))
        self.assertIsNone(normalize_port(float("inf")))
        self.assertIsNone(normalize_port(float("-inf")))

    def test_zeroconf_backend_decodes_packed_addresses(self) -> None:
        events: list[tuple[str, dict[str, object]]] = []
        backend = ZeroconfBackend(
            "_expra-peer._tcp.local.",
            lambda event, payload: events.append((event, payload)),
        )
        info = SimpleNamespace(addresses=[b"\xc0\xa8\x01\x02"], port=27321)

        backend._emit(
            SimpleNamespace(get_service_info=lambda *_args: info),
            "type",
            "peer",
            "add",
        )

        self.assertEqual(events[0][1]["addresses"], ("192.168.1.2",))

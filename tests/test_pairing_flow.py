import unittest
from unittest.mock import patch

from expra_connect.identity import NodeId, NodeIdentity
from expra_connect.models import (
    DiscoveredNodeCandidate,
    EndpointCandidate,
    EndpointSource,
)
from expra_connect.pairing import PairingManager
from expra_connect.pairing_flow import NetworkPairing


class NetworkPairingTests(unittest.TestCase):
    def test_transport_failure_falls_back_to_another_discovered_endpoint(self) -> None:
        identity = NodeIdentity.create(NodeId("local-node"))
        peer_id = NodeId("peer-node")
        endpoints = (
            EndpointCandidate("192.168.1.20", 27321, source=EndpointSource.IPV4),
            EndpointCandidate("10.0.0.20", 27321, source=EndpointSource.VPN),
        )
        candidate = DiscoveredNodeCandidate(
            stable_id=peer_id.value,
            hostname="peer.local",
            addresses=tuple(endpoint.address for endpoint in endpoints),
            port=27321,
            service_name="peer._expra-peer._tcp.local.",
            app_version="0.6.0.0",
            protocol_version="1",
            platform="linux",
            connectable=True,
            compatible=True,
            last_seen=1.0,
            transport_fingerprint="peer-tls",
            endpoint_candidates=endpoints,
        )
        pairing = PairingManager(identity.node_id)
        network_pairing = NetworkPairing(
            identity=identity,
            transport_fingerprint="local-tls",
            root_public_key=identity.root_public_key,
            pairing=pairing,
            candidates={peer_id.value: candidate},
            persist=lambda: True,
        )
        attempts: list[str] = []

        def build_transport(address: str, _port: int, **_: object) -> object:
            attempts.append(address)
            if address == "192.168.1.20":
                raise TimeoutError("route timed out")
            return object()

        response = {
            "transaction_id": "remote-transaction",
            "caller_node_id": identity.node_id.value,
            "identity_fingerprint": "local-identity",
            "transport_fingerprint": "peer-tls",
            "root_public_key": identity.root_public_key,
            "transport_generation": 1,
            "transport_proof": "proof",
            "secret": "shared-secret",
            "permissions": ["read_state"],
            "expires_at": 9999999999.0,
        }

        with (
            patch(
                "expra_connect.pairing_flow.TLSRemoteTransport",
                side_effect=build_transport,
            ),
            patch(
                "expra_connect.pairing_flow.AuthenticatedNodeProvider.request_pairing",
                return_value=response,
            ),
            patch(
                "expra_connect.pairing_flow.AuthenticatedNodeProvider.confirm_pairing",
                return_value=True,
            ),
        ):
            network_pairing.pair(peer_id, permissions=frozenset({"read_state"}))

        self.assertEqual(attempts, ["192.168.1.20", "10.0.0.20"])


if __name__ == "__main__":
    unittest.main()

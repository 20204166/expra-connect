import unittest

from expra_connect.connection_state import ConnectionState, ConnectionStatus
from expra_connect.identity import NodeId
from expra_connect.models import NodeCapability
from expra_connect.registry import (
    MembershipState,
    NodeRegistry,
    TrustState,
)
from expra_connect.role_engine import ClusterRole


class RegistryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.registry = NodeRegistry(NodeId("local-node"))
        self.peer = NodeId("peer")

    def test_discovery_does_not_grant_trust_or_capability_authority(self) -> None:
        record = self.registry.observe(
            self.peer, frozenset({NodeCapability.READ_STATE})
        )
        self.assertEqual(record.trust, TrustState.DISCOVERED)
        self.assertEqual(record.membership, MembershipState.NOT_JOINED)

    def test_connection_is_independent_from_trust(self) -> None:
        self.registry.observe(self.peer, frozenset())
        record = self.registry.set_connection(
            self.peer, ConnectionState.online(now=10.0)
        )
        self.assertEqual(record.trust, TrustState.DISCOVERED)
        self.assertEqual(record.connection.status, ConnectionStatus.ONLINE)

    def test_pairing_then_join_are_explicit_transitions(self) -> None:
        self.registry.observe(self.peer, frozenset())
        self.registry.promote(
            self.peer, permissions=frozenset({NodeCapability.READ_STATE})
        )
        record = self.registry.join(
            self.peer,
            role=ClusterRole.WORKER,
            coordinator_id=NodeId("local-node"),
        )
        self.assertEqual(record.trust, TrustState.AUTHORIZED)
        self.assertEqual(record.membership, MembershipState.WORKER)
        self.assertEqual(record.coordinator_id, NodeId("local-node"))

    def test_revoke_does_not_delete_membership_or_connection(self) -> None:
        self.registry.observe(self.peer, frozenset())
        self.registry.promote(self.peer, permissions=frozenset())
        self.registry.join(
            self.peer, role=ClusterRole.WORKER, coordinator_id=NodeId("local-node")
        )
        self.registry.set_connection(self.peer, ConnectionState.offline("test"))
        record = self.registry.revoke(self.peer)
        self.assertEqual(record.trust, TrustState.REVOKED)
        self.assertEqual(record.membership, MembershipState.WORKER)
        self.assertEqual(record.connection.status, ConnectionStatus.OFFLINE)

    def test_revoked_node_cannot_be_promoted_without_reobservation(self) -> None:
        self.registry.observe(self.peer, frozenset())
        self.registry.revoke(self.peer)
        with self.assertRaises(PermissionError):
            self.registry.promote(self.peer, permissions=frozenset())

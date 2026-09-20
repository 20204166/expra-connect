import unittest

from expra_connect.identity import NodeId
from expra_connect.pairing import PairingManager, PeerGrant, TrustedPeer


class PairingTests(unittest.TestCase):
    def test_pairing_is_directional_but_relationship_is_normalized(self) -> None:
        manager = PairingManager(NodeId("peer-a"))
        transaction = manager.begin(NodeId("peer-b"), "secret")
        manager.approve(transaction.transaction_id, frozenset({"ping"}))
        manager.confirm(transaction.transaction_id)
        self.assertTrue(manager.has_relationship(NodeId("peer-b")))
        self.assertTrue(manager.can_call(NodeId("peer-b"), "ping"))
        self.assertFalse(manager.can_call(NodeId("peer-b"), "admin"))

    def test_relationship_api_accepts_either_direction_without_reverse_trust(
        self,
    ) -> None:
        peer = NodeId("peer-b")
        outbound = PairingManager(NodeId("peer-a"))
        outbound.trusted[peer] = TrustedPeer(peer, "secret", frozenset())
        self.assertTrue(outbound.has_valid_relationship(peer))
        self.assertTrue(outbound.can_join_cluster(peer))
        self.assertNotIn(NodeId("peer-a"), outbound.grants)

        inbound = PairingManager(NodeId("peer-a"))
        inbound.grants[peer] = PeerGrant(peer, "secret", frozenset())
        self.assertTrue(inbound.has_valid_relationship(peer))
        self.assertTrue(inbound.can_join_cluster(peer))
        self.assertNotIn(peer, inbound.trusted)

    def test_approve_rejects_non_read_only_permissions(self) -> None:
        manager = PairingManager(NodeId("peer-a"))
        transaction = manager.begin(NodeId("peer-b"), "secret")
        with self.assertRaises(ValueError):
            manager.approve(transaction.transaction_id, frozenset({"admin"}))

    def test_confirm_preserves_all_transport_metadata(self) -> None:
        manager = PairingManager(NodeId("peer-a"))
        transaction = manager.begin(
            NodeId("peer-b"),
            "secret",
            identity_fingerprint="identity",
            transport_fingerprint="transport",
            root_public_key="root",
            transport_generation=3,
            transport_proof="proof",
        )
        manager.approve(transaction.transaction_id, frozenset({"read_state"}))
        trusted = manager.confirm(transaction.transaction_id)
        self.assertEqual(trusted.identity_fingerprint, "identity")
        self.assertEqual(trusted.transport_fingerprint, "transport")
        self.assertEqual(trusted.root_public_key, "root")
        self.assertEqual(trusted.transport_generation, 3)
        self.assertEqual(trusted.transport_proof, "proof")

    def test_find_pending_only_returns_exact_active_transaction(self) -> None:
        now = [100.0]
        manager = PairingManager(NodeId("peer-a"), clock=lambda: now[0], ttl=5.0)
        transaction = manager.begin(NodeId("peer-b"), "secret")
        self.assertEqual(manager.find_pending(NodeId("peer-b"), "secret"), transaction)
        self.assertIsNone(manager.find_pending(NodeId("peer-b"), "other"))
        now[0] = 106.0
        self.assertIsNone(manager.find_pending(NodeId("peer-b"), "secret"))

    def test_expired_pairing_cannot_confirm(self) -> None:
        manager = PairingManager(NodeId("peer-a"), clock=lambda: 100.0, ttl=5.0)
        transaction = manager.begin(NodeId("peer-b"), "secret")
        with self.assertRaises(ValueError):
            manager.confirm(transaction.transaction_id, now=106.0)

    def test_initiator_and_acceptor_keep_directional_records(self) -> None:
        initiator = PairingManager(NodeId("peer-a"))
        acceptor = PairingManager(NodeId("peer-b"))
        transaction = initiator.begin(NodeId("peer-b"), "secret")
        acceptor.receive_request(NodeId("peer-a"), transaction)
        grant = acceptor.approve(transaction.transaction_id, frozenset({"ping"}))
        initiator.accept_grant(transaction.transaction_id, NodeId("peer-b"), grant)
        self.assertTrue(initiator.can_call(NodeId("peer-b"), "ping"))
        self.assertIn(NodeId("peer-a"), acceptor.grants)
        self.assertNotIn(NodeId("peer-a"), acceptor.trusted)

    def test_approved_transaction_expiry_is_pruned(self) -> None:
        manager = PairingManager(NodeId("peer-a"), clock=lambda: 100.0, ttl=5.0)
        transaction = manager.begin(
            NodeId("peer-b"),
            "secret",
            identity_fingerprint="identity-a",
            transport_fingerprint="tls-a",
        )
        manager.approve(transaction.transaction_id, frozenset({"ping"}))
        self.assertEqual(manager.prune_expired(now=104.0), 0)
        self.assertEqual(manager.prune_expired(now=105.0), 1)

    def test_revoke_removes_grant_trust_and_pending_transactions(self) -> None:
        manager = PairingManager(NodeId("peer-a"))
        transaction = manager.begin(NodeId("peer-b"), "secret")
        manager.approve(transaction.transaction_id, frozenset({"ping"}))
        manager.confirm(transaction.transaction_id)
        pending = manager.begin(NodeId("peer-b"), "another-secret")
        manager.revoke(NodeId("peer-b"))
        self.assertFalse(manager.has_relationship(NodeId("peer-b")))
        self.assertFalse(manager.can_call(NodeId("peer-b"), "ping"))
        self.assertFalse(manager.pending)
        self.assertNotIn(pending.transaction_id, manager.pending)

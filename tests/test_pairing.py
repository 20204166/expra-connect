import unittest

from expra_connect.identity import NodeId
from expra_connect.pairing import PairingManager


class PairingTests(unittest.TestCase):
    def test_pairing_is_directional_but_relationship_is_normalized(self) -> None:
        manager = PairingManager(NodeId("peer-a"))
        transaction = manager.begin(NodeId("peer-b"), "secret")
        manager.approve(transaction.transaction_id, frozenset({"ping"}))
        manager.confirm(transaction.transaction_id)
        self.assertTrue(manager.has_relationship(NodeId("peer-b")))
        self.assertTrue(manager.can_call(NodeId("peer-b"), "ping"))
        self.assertFalse(manager.can_call(NodeId("peer-b"), "admin"))

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

    def test_fingerprint_binding_and_expiry_pruning(self) -> None:
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
        manager.revoke(NodeId("peer-b"))
        self.assertFalse(manager.has_relationship(NodeId("peer-b")))
        self.assertFalse(manager.can_call(NodeId("peer-b"), "ping"))

import unittest

from expra_connect.identity import NodeId, NodeIdentity
from expra_connect.pairing import (
    PairingBusy,
    PairingManager,
    PeerGrant,
    RelationshipState,
    TrustedPeer,
)


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

    def test_begin_rejects_a_competing_transaction_for_the_same_direction(
        self,
    ) -> None:
        manager = PairingManager(NodeId("peer-a"))
        peer = NodeId("peer-b")
        manager.begin(peer, "secret-one", direction="outbound")
        with self.assertRaises(PairingBusy):
            manager.begin(peer, "secret-two", direction="outbound")
        # The opposite direction is an independent relationship axis.
        manager.begin(peer, "secret-three", direction="inbound")

    def test_exact_replay_returns_the_existing_transaction(self) -> None:
        manager = PairingManager(NodeId("peer-a"))
        peer = NodeId("peer-b")
        first = manager.begin(peer, "replayed-secret", direction="outbound")
        replay = manager.begin(peer, "replayed-secret", direction="outbound")
        self.assertEqual(replay.transaction_id, first.transaction_id)
        self.assertEqual(len(manager.pending), 1)

    def test_relationship_distinguishes_directions(self) -> None:
        peer = NodeId("peer-b")
        manager = PairingManager(NodeId("peer-a"))
        self.assertIs(manager.relationship(peer), RelationshipState.UNPAIRED)
        manager.grants[peer] = PeerGrant(peer, "s" * 64, frozenset())
        self.assertIs(manager.relationship(peer), RelationshipState.INBOUND_GRANTED)
        manager.trusted[peer] = TrustedPeer(peer, "t" * 64, frozenset())
        self.assertIs(manager.relationship(peer), RelationshipState.BIDIRECTIONAL)
        del manager.grants[peer]
        self.assertIs(manager.relationship(peer), RelationshipState.OUTBOUND_TRUSTED)

    def test_auth_failure_marks_outbound_trust_for_repair_only(self) -> None:
        peer = NodeId("peer-b")
        manager = PairingManager(NodeId("peer-a"))
        manager.trusted[peer] = TrustedPeer(peer, "t" * 64, frozenset())
        manager.mark_auth_failure(peer)
        self.assertIs(manager.relationship(peer), RelationshipState.REPAIR_REQUIRED)
        self.assertTrue(manager.is_auth_broken(peer))
        manager.mark_auth_ok(peer)
        self.assertIs(manager.relationship(peer), RelationshipState.OUTBOUND_TRUSTED)

    def test_auth_failure_without_outbound_trust_is_ignored(self) -> None:
        peer = NodeId("peer-b")
        manager = PairingManager(NodeId("peer-a"))
        manager.mark_auth_failure(peer)
        self.assertFalse(manager.is_auth_broken(peer))

    def test_classify_trusted_candidate_requires_stored_root_proof(self) -> None:
        local = NodeIdentity.create(NodeId("peer-a"))
        peer_identity = NodeIdentity.create(NodeId("peer-b"))
        peer = peer_identity.node_id
        manager = PairingManager(local.node_id)
        manager.trusted[peer] = TrustedPeer(
            peer,
            "t" * 64,
            frozenset(),
            root_public_key=peer_identity.root_public_key,
            transport_fingerprint="tls-old",
            transport_generation=1,
        )
        # A valid forward rotation is healthy, not a repair.
        self.assertIs(
            manager.classify_trusted_candidate(
                peer,
                candidate_root_public_key=peer_identity.root_public_key,
                candidate_transport_fingerprint="tls-new",
                candidate_transport_generation=2,
                candidate_transport_proof=peer_identity.sign_transport_proof(
                    2, "tls-new"
                ),
            ),
            RelationshipState.OUTBOUND_TRUSTED,
        )
        # Same claimed root but an unprovable binding is not trusted.
        self.assertIs(
            manager.classify_trusted_candidate(
                peer,
                candidate_root_public_key=peer_identity.root_public_key,
                candidate_transport_fingerprint="tls-rogue",
                candidate_transport_generation=3,
                candidate_transport_proof="forged",
            ),
            RelationshipState.IDENTITY_CONFLICT,
        )
        # A different root is a hard identity conflict.
        other = NodeIdentity.create(NodeId("peer-b"))
        self.assertIs(
            manager.classify_trusted_candidate(
                peer,
                candidate_root_public_key=other.root_public_key,
                candidate_transport_fingerprint="tls-old",
                candidate_transport_generation=1,
                candidate_transport_proof=other.sign_transport_proof(1, "tls-old"),
            ),
            RelationshipState.IDENTITY_CONFLICT,
        )

    def test_revoke_trusted_preserves_the_inbound_grant(self) -> None:
        peer = NodeId("peer-b")
        manager = PairingManager(NodeId("peer-a"))
        manager.trusted[peer] = TrustedPeer(peer, "t" * 64, frozenset())
        manager.grants[peer] = PeerGrant(peer, "g" * 64, frozenset())
        manager.revoke_trusted(peer)
        self.assertNotIn(peer, manager.trusted)
        self.assertIn(peer, manager.grants)

    def test_expiring_a_repair_transaction_keeps_established_trust(self) -> None:
        now = [100.0]
        peer = NodeId("peer-b")
        manager = PairingManager(NodeId("peer-a"), clock=lambda: now[0], ttl=5.0)
        manager.trusted[peer] = TrustedPeer(peer, "t" * 64, frozenset())
        manager.begin(peer, "repair-secret", direction="outbound", intent="repair")
        now[0] = 200.0
        self.assertEqual(manager.prune_expired(), 1)
        self.assertIn(peer, manager.trusted)
        self.assertIs(manager.relationship(peer), RelationshipState.OUTBOUND_TRUSTED)


class PairingTransactionBindingTests(unittest.TestCase):
    """Confirm/abort binding, replay, direction, and expiry-boundary invariants."""

    def _approved(self, manager: PairingManager, peer: NodeId) -> str:
        transaction = manager.begin(peer, "s" * 64)
        manager.approve(transaction.transaction_id, frozenset({"ping"}))
        return transaction.transaction_id

    def _grant(self, **overrides: object) -> PeerGrant:
        fields: dict[str, object] = {
            "caller_id": NodeId("peer-a"),
            "secret": "s" * 64,
            "permissions": frozenset({"ping"}),
            "identity_fingerprint": "identity",
            "transport_fingerprint": "transport",
            "root_public_key": "root",
            "transport_generation": 1,
            "transport_proof": "proof",
        }
        fields.update(overrides)
        return PeerGrant(**fields)  # type: ignore[arg-type]

    def test_accept_grant_rejects_each_field_substitution(self) -> None:
        manager = PairingManager(NodeId("peer-a"))
        peer = NodeId("peer-b")
        transaction = manager.begin(
            peer,
            "s" * 64,
            identity_fingerprint="identity",
            transport_fingerprint="transport",
            root_public_key="root",
            transport_generation=1,
            transport_proof="proof",
        )
        substitutions: tuple[dict[str, object], ...] = (
            {"identity_fingerprint": "other-identity"},
            {"transport_fingerprint": "other-transport"},
            {"root_public_key": "other-root"},
            {"transport_generation": 2},
            {"transport_proof": "other-proof"},
            {"caller_id": NodeId("peer-x")},
        )
        for override in substitutions:
            with self.subTest(override=override):
                with self.assertRaises(ValueError):
                    manager.accept_grant(
                        transaction.transaction_id, peer, self._grant(**override)
                    )
                self.assertNotIn(peer, manager.trusted)
                self.assertIn(transaction.transaction_id, manager.pending)

        with self.assertRaises(ValueError):
            manager.accept_grant(
                transaction.transaction_id, NodeId("peer-x"), self._grant()
            )

    def test_confirm_replay_after_commit_is_rejected(self) -> None:
        manager = PairingManager(NodeId("peer-a"))
        peer = NodeId("peer-b")
        transaction_id = self._approved(manager, peer)
        trusted = manager.confirm(transaction_id)
        with self.assertRaises(ValueError):
            manager.confirm(transaction_id)
        self.assertIs(manager.trusted[peer], trusted)
        self.assertNotIn(transaction_id, manager.pending)

    def test_confirm_after_abort_is_rejected(self) -> None:
        manager = PairingManager(NodeId("peer-a"))
        peer = NodeId("peer-b")
        transaction_id = self._approved(manager, peer)
        manager.abort(transaction_id)
        with self.assertRaises(ValueError):
            manager.confirm(transaction_id)
        self.assertNotIn(peer, manager.trusted)

    def test_stale_confirm_cannot_activate_a_newer_transaction(self) -> None:
        manager = PairingManager(NodeId("peer-a"))
        peer = NodeId("peer-b")
        stale = self._approved(manager, peer)
        manager.abort(stale)
        newer = manager.begin(peer, "n" * 64)

        with self.assertRaises(ValueError):
            manager.confirm(stale)

        self.assertNotIn(peer, manager.trusted)
        self.assertIn(newer.transaction_id, manager.pending)

    def test_same_direction_pair_and_repair_are_mutually_exclusive(self) -> None:
        manager = PairingManager(NodeId("peer-a"))
        peer = NodeId("peer-b")
        first = manager.begin(peer, "p" * 64, direction="outbound", intent="pair")
        with self.assertRaises(PairingBusy):
            manager.begin(peer, "r" * 64, direction="outbound", intent="repair")
        self.assertEqual(manager.begin(peer, "p" * 64, intent="pair"), first)

        repair = PairingManager(NodeId("peer-a"))
        repair.begin(peer, "r" * 64, direction="outbound", intent="repair")
        with self.assertRaises(PairingBusy):
            repair.begin(peer, "r" * 64, direction="outbound", intent="pair")

    def test_replay_does_not_cross_pair_and_repair_intent(self) -> None:
        manager = PairingManager(NodeId("peer-a"))
        peer = NodeId("peer-b")
        manager.begin(peer, "shared" * 8, direction="outbound", intent="pair")
        with self.assertRaises(PairingBusy):
            manager.begin(peer, "shared" * 8, direction="outbound", intent="repair")

    def test_revoke_trusted_clears_only_the_outbound_pending_transaction(self) -> None:
        manager = PairingManager(NodeId("peer-a"))
        peer = NodeId("peer-b")
        outbound = manager.begin(peer, "o" * 64, direction="outbound")
        inbound = manager.begin(peer, "i" * 64, direction="inbound")

        manager.revoke_trusted(peer)

        self.assertNotIn(outbound.transaction_id, manager.pending)
        self.assertIn(inbound.transaction_id, manager.pending)
        manager.revoke_grant(peer)
        self.assertNotIn(inbound.transaction_id, manager.pending)

    def test_confirm_at_exact_expiry_boundary_is_rejected(self) -> None:
        now = [100.0]
        manager = PairingManager(NodeId("peer-a"), clock=lambda: now[0], ttl=5.0)
        peer = NodeId("peer-b")
        transaction = manager.begin(peer, "s" * 64)
        manager.approve(transaction.transaction_id, frozenset({"ping"}))

        with self.assertRaises(ValueError):
            manager.confirm(transaction.transaction_id, now=105.0)

        self.assertNotIn(peer, manager.trusted)

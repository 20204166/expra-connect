"""Directional Pair lifecycle, explicit repair, and fail-closed boundaries."""

import json
import tempfile
import threading
import time
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from expra_connect import __version__
from expra_connect.identity import NodeId, NodeIdentity, node_identity_fingerprint
from expra_connect.models import DiscoveredNodeCandidate, NodePermission
from expra_connect.pairing import (
    IdentityConflict,
    PeerGrant,
    RelationshipState,
    RepairNotRequired,
    RepairRequired,
)
from expra_connect.runtime import ConnectConfig, ConnectRuntime
from expra_connect.wire_protocol import PairingRequest, RemoteProtocolError


def _candidate(target: ConnectRuntime) -> DiscoveredNodeCandidate:
    identity = target.identity
    generations = target.transport_generations
    status = target.status
    assert identity is not None
    assert generations is not None
    assert status.bound_port is not None
    assert status.tls_fingerprint is not None
    return DiscoveredNodeCandidate(
        stable_id=identity.node_id.value,
        hostname="127.0.0.1",
        addresses=("127.0.0.1",),
        port=status.bound_port,
        service_name="_expra-peer._tcp.local.",
        app_version=__version__,
        protocol_version="1",
        platform="linux",
        connectable=True,
        compatible=True,
        last_seen=time.time(),
        identity_fingerprint=node_identity_fingerprint(identity.node_id),
        transport_fingerprint=status.tls_fingerprint,
        root_public_key=identity.root_public_key,
        transport_generation=generations.current.generation,
        transport_proof=generations.current.proof,
    )


class _Pair:
    def __init__(self, directory: str) -> None:
        self.count = 0

        def approve(request: object) -> bool:
            self.count += 1
            return True

        self.first = ConnectRuntime(
            ConnectConfig(
                profile_dir=Path(directory) / "first",
                discovery_enabled=False,
                preferred_port=0,
                on_pairing_request=lambda _request: True,
            )
        )
        self.second = ConnectRuntime(
            ConnectConfig(
                profile_dir=Path(directory) / "second",
                discovery_enabled=False,
                preferred_port=0,
                on_pairing_request=approve,
            )
        )
        self.first.start()
        self.second.start()
        assert self.first.identity is not None
        assert self.second.identity is not None
        self.first_id = self.first.identity.node_id
        self.peer = self.second.identity.node_id

    def observe(self) -> None:
        self.first._peers[self.peer.value] = _candidate(self.second)

    def close(self) -> None:
        self.first.shutdown()
        self.second.shutdown()


class PeerLifecycleTests(unittest.TestCase):
    def test_already_paired_pair_does_not_rotate_the_secret(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            nodes = _Pair(directory)
            try:
                nodes.observe()
                first_trusted = nodes.first.pair_peer(nodes.peer)
                assert nodes.first.pairing is not None
                assert nodes.second.pairing is not None
                old = nodes.first.pairing.trusted[nodes.peer].secret
                callback_count = nodes.count

                again = nodes.first.pair_peer(nodes.peer)

                self.assertEqual(again.secret, old)
                self.assertEqual(nodes.first.pairing.trusted[nodes.peer].secret, old)
                self.assertFalse(nodes.first.pairing.pending)
                self.assertEqual(nodes.count, callback_count)
                self.assertEqual(
                    nodes.second.pairing.grants[nodes.first_id].secret,
                    old,
                )
                self.assertEqual(first_trusted.secret, old)
            finally:
                nodes.close()

    def test_inbound_only_grant_does_not_block_outbound_pair(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            nodes = _Pair(directory)
            try:
                nodes.observe()
                nodes.first.pair_peer(nodes.peer)
                assert nodes.second.pairing is not None
                assert nodes.first.pairing is not None
                # Second has an inbound grant only; it may still pair outbound.
                self.assertIn(nodes.first_id, nodes.second.pairing.grants)
                self.assertNotIn(nodes.first_id, nodes.second.pairing.trusted)
                nodes.second._peers[nodes.first_id.value] = _candidate(nodes.first)

                nodes.second.pair_peer(nodes.first_id)

                self.assertIn(nodes.first_id, nodes.second.pairing.trusted)
                self.assertIn(nodes.peer, nodes.first.pairing.grants)
                self.assertIs(
                    nodes.second.peer_relationship(nodes.first_id),
                    RelationshipState.BIDIRECTIONAL,
                )
            finally:
                nodes.close()

    def test_repair_replaces_broken_credentials_for_the_same_root(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            nodes = _Pair(directory)
            try:
                nodes.observe()
                nodes.first.pair_peer(nodes.peer)
                assert nodes.first.pairing is not None
                assert nodes.second.pairing is not None
                old = nodes.first.pairing.trusted[nodes.peer].secret
                old_root = nodes.first.pairing.trusted[nodes.peer].root_public_key
                # Controlled fixture: the target lost the caller's grant and the
                # caller observed an authentication failure.
                nodes.second.pairing.revoke_grant(nodes.first_id)
                nodes.first.pairing.mark_auth_failure(nodes.peer)

                self.assertIs(
                    nodes.first.peer_relationship(nodes.peer),
                    RelationshipState.REPAIR_REQUIRED,
                )
                with self.assertRaises(RepairRequired):
                    nodes.first.pair_peer(nodes.peer)

                repaired = nodes.first.repair_peer(nodes.peer)

                self.assertNotEqual(repaired.secret, old)
                self.assertEqual(
                    nodes.first.pairing.trusted[nodes.peer].root_public_key, old_root
                )
                self.assertEqual(
                    nodes.second.pairing.grants[nodes.first_id].secret,
                    repaired.secret,
                )
                self.assertIs(
                    nodes.first.peer_relationship(nodes.peer),
                    RelationshipState.OUTBOUND_TRUSTED,
                )
                provider = nodes.first.connect_peer(nodes.peer)
                self.assertEqual(provider.hello()["node_id"], nodes.peer.value)
            finally:
                nodes.close()

    def test_repair_cancellation_preserves_the_old_trust(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            nodes = _Pair(directory)
            try:
                nodes.observe()
                nodes.first.pair_peer(nodes.peer)
                assert nodes.first.pairing is not None
                assert nodes.second.pairing is not None
                old = nodes.first.pairing.trusted[nodes.peer].secret
                nodes.second.pairing.revoke_grant(nodes.first_id)
                nodes.first.pairing.mark_auth_failure(nodes.peer)
                cancelled = threading.Event()
                cancelled.set()

                with self.assertRaises(RuntimeError):
                    nodes.first.repair_peer(nodes.peer, cancel_event=cancelled)

                self.assertEqual(nodes.first.pairing.trusted[nodes.peer].secret, old)
                self.assertFalse(nodes.first.pairing.pending)
            finally:
                nodes.close()

    def test_repair_is_rejected_when_not_required(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            nodes = _Pair(directory)
            try:
                nodes.observe()
                nodes.first.pair_peer(nodes.peer)
                with self.assertRaises(RepairNotRequired):
                    nodes.first.repair_peer(nodes.peer)
            finally:
                nodes.close()

    def test_repair_preserves_the_unrelated_reverse_direction(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            nodes = _Pair(directory)
            try:
                nodes.observe()
                nodes.first.pair_peer(nodes.peer)
                assert nodes.first.pairing is not None
                assert nodes.second.pairing is not None
                reverse_grant = PeerGrant(
                    nodes.peer, "b" * 64, frozenset({"read_state"})
                )
                nodes.first.pairing.grants[nodes.peer] = reverse_grant
                nodes.second.pairing.revoke_grant(nodes.first_id)
                nodes.first.pairing.mark_auth_failure(nodes.peer)

                nodes.first.repair_peer(nodes.peer)

                self.assertEqual(
                    nodes.first.pairing.grants[nodes.peer].secret, reverse_grant.secret
                )
            finally:
                nodes.close()

    def test_repair_persistence_failure_keeps_the_old_trust(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            nodes = _Pair(directory)
            try:
                nodes.observe()
                nodes.first.pair_peer(nodes.peer)
                assert nodes.first.pairing is not None
                assert nodes.second.pairing is not None
                old = nodes.first.pairing.trusted[nodes.peer].secret
                nodes.second.pairing.revoke_grant(nodes.first_id)
                nodes.first.pairing.mark_auth_failure(nodes.peer)
                assert nodes.first._network_pairing is not None

                with (
                    patch.object(
                        nodes.first._network_pairing, "_persist", return_value=False
                    ),
                    self.assertRaises(RuntimeError),
                ):
                    nodes.first.repair_peer(nodes.peer)

                self.assertEqual(nodes.first.pairing.trusted[nodes.peer].secret, old)
                self.assertNotIn(nodes.first_id, nodes.second.pairing.grants)
            finally:
                nodes.close()

    def test_identity_conflict_denies_pair_and_repair(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            nodes = _Pair(directory)
            try:
                nodes.observe()
                nodes.first.pair_peer(nodes.peer)
                assert nodes.first.pairing is not None
                assert nodes.second.identity is not None
                old = nodes.first.pairing.trusted[nodes.peer].secret
                impostor = NodeIdentity.create(nodes.peer)
                rogue = replace(
                    _candidate(nodes.second),
                    root_public_key=impostor.root_public_key,
                    transport_fingerprint="rogue-tls",
                    transport_generation=1,
                    transport_proof=impostor.sign_transport_proof(1, "rogue-tls"),
                )
                nodes.first._peers[nodes.peer.value] = rogue

                with self.assertRaises(IdentityConflict):
                    nodes.first.pair_peer(nodes.peer)
                with self.assertRaises(IdentityConflict):
                    nodes.first.repair_peer(nodes.peer)

                self.assertEqual(nodes.first.pairing.trusted[nodes.peer].secret, old)
                self.assertEqual(
                    nodes.first.pairing.trusted[nodes.peer].root_public_key,
                    nodes.second.identity.root_public_key,
                )
            finally:
                nodes.close()

    def test_revoke_then_fresh_pair_is_allowed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            nodes = _Pair(directory)
            try:
                nodes.observe()
                nodes.first.pair_peer(nodes.peer)
                # Revoke the relationship on both nodes as the host would.
                nodes.first.revoke_peer(nodes.peer)
                nodes.second.revoke_peer(nodes.first_id)
                assert nodes.first.pairing is not None
                self.assertNotIn(nodes.peer, nodes.first.pairing.trusted)

                nodes.observe()
                nodes.first.pair_peer(nodes.peer)

                self.assertIn(nodes.peer, nodes.first.pairing.trusted)
                assert nodes.second.pairing is not None
                self.assertIn(nodes.first_id, nodes.second.pairing.grants)
            finally:
                nodes.close()

    def test_reconnect_never_pairs(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            nodes = _Pair(directory)
            try:
                nodes.observe()
                nodes.first.pair_peer(nodes.peer)
                assert nodes.first.pairing is not None
                nodes.first.connect_peer(nodes.peer)
                callback_count = nodes.count
                nodes.first.disconnect_peer(nodes.peer)

                nodes.first.reconnect_peer(nodes.peer)

                self.assertEqual(nodes.count, callback_count)
                self.assertFalse(nodes.first.pairing.pending)
            finally:
                nodes.close()

    def test_pairing_diagnostics_are_secret_free_and_deterministic(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            nodes = _Pair(directory)
            try:
                nodes.observe()
                nodes.first.pair_peer(nodes.peer)
                assert nodes.first.pairing is not None
                secret = nodes.first.pairing.trusted[nodes.peer].secret

                first = nodes.first.pairing_diagnostics(nodes.peer)
                second = nodes.first.pairing_diagnostics(nodes.peer)

                self.assertEqual(first, second)
                serialized = json.dumps(first, sort_keys=True)
                self.assertNotIn(secret, serialized)
                self.assertEqual(first["relationship"], "outbound_trusted")
                self.assertTrue(first["outbound"]["trusted"])
                self.assertFalse(first["inbound"]["granted"])
                self.assertIsNotNone(first["identity"]["known_root_fingerprint"])
            finally:
                nodes.close()


class TargetPairRequestTests(unittest.TestCase):
    def setUp(self) -> None:
        self._directory = tempfile.TemporaryDirectory()
        self.runtime = ConnectRuntime(
            ConnectConfig(
                profile_dir=Path(self._directory.name),
                discovery_enabled=False,
                preferred_port=0,
                on_pairing_request=lambda _request: True,
            )
        )
        self.runtime.start()
        self.caller = NodeIdentity.create(NodeId("caller-node"))
        assert self.runtime.pairing is not None
        self.runtime.pairing.grants[self.caller.node_id] = PeerGrant(
            self.caller.node_id,
            "a" * 64,
            frozenset({"read_state"}),
            root_public_key=self.caller.root_public_key,
            transport_fingerprint="caller-tls",
            transport_generation=1,
        )

    def tearDown(self) -> None:
        self.runtime.shutdown()
        self._directory.cleanup()

    def _request(
        self,
        intent: str,
        *,
        root: str | None = None,
        proof: str | None = None,
        secret: str = "b" * 64,
    ) -> PairingRequest:
        return PairingRequest(
            caller_node_id=self.caller.node_id,
            identity_fingerprint=node_identity_fingerprint(self.caller.node_id),
            transport_fingerprint="caller-tls",
            proposed_secret=secret,
            permissions=frozenset({NodePermission.READ_STATE}),
            root_public_key=root or self.caller.root_public_key,
            transport_generation=1,
            transport_proof=proof or self.caller.sign_transport_proof(1, "caller-tls"),
            intent=intent,
        )

    def test_existing_grant_returns_already_paired_for_plain_pair(self) -> None:
        response = self.runtime._handle_pairing_request(self._request("pair"))
        self.assertFalse(response["approved"])
        self.assertEqual(response["outcome"], "already_paired")

    def test_existing_grant_permits_explicit_repair(self) -> None:
        response = self.runtime._handle_pairing_request(self._request("repair"))
        self.assertTrue(response["approved"])
        assert self.runtime.pairing is not None
        pending = self.runtime.pairing.pending[response["transaction_id"]]
        self.assertEqual(pending.intent, "repair")
        self.assertEqual(pending.direction, "inbound")

    def test_conflicting_or_unproven_root_is_an_identity_conflict(self) -> None:
        impostor = NodeIdentity.create(self.caller.node_id)
        conflicting = self.runtime._handle_pairing_request(
            self._request(
                "repair",
                root=impostor.root_public_key,
                proof=impostor.sign_transport_proof(1, "caller-tls"),
            )
        )
        self.assertEqual(conflicting["outcome"], "identity_conflict")
        malformed = self.runtime._handle_pairing_request(
            self._request("repair", proof="not-a-proof")
        )
        self.assertEqual(malformed["outcome"], "identity_conflict")

    def test_duplicate_repair_is_an_exact_replay_or_already_pending(self) -> None:
        first = self.runtime._handle_pairing_request(self._request("repair"))
        replay = self.runtime._handle_pairing_request(
            self._request("repair", secret=first["secret"])
        )
        self.assertEqual(replay["transaction_id"], first["transaction_id"])
        competing = self.runtime._handle_pairing_request(
            self._request("repair", secret="c" * 64)
        )
        self.assertEqual(competing["outcome"], "already_pending")

    def test_unknown_intent_is_rejected_by_the_protocol(self) -> None:
        with self.assertRaises(RemoteProtocolError):
            self._request("upgrade")


class TransportRotationTests(unittest.TestCase):
    def test_rotation_never_requires_pair_or_repair(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            nodes = _Pair(directory)
            try:
                nodes.observe()
                nodes.first.pair_peer(nodes.peer)
                assert nodes.first.pairing is not None
                old = nodes.first.pairing.trusted[nodes.peer].secret

                nodes.second.rotate_transport()
                nodes.observe()

                self.assertIsNot(
                    nodes.first.peer_relationship(nodes.peer),
                    RelationshipState.REPAIR_REQUIRED,
                )
                # A subsequent pair reports already-paired without rotating.
                self.assertEqual(nodes.first.pair_peer(nodes.peer).secret, old)
                nodes.first.reconnect_peer(nodes.peer)
                self.assertEqual(nodes.first.pairing.trusted[nodes.peer].secret, old)
            finally:
                nodes.close()


if __name__ == "__main__":
    unittest.main()

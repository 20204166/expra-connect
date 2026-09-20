import tempfile
import unittest
from pathlib import Path

from expra_connect.identity import (
    NodeId,
    NodeIdentity,
    RotationConflict,
    TransportGenerationManager,
    TransportStateError,
)
from expra_connect.persistence import JsonStateStore, StateDataError


class IdentityRotationTests(unittest.TestCase):
    def _manager(self, directory: str) -> TransportGenerationManager:
        identity = NodeIdentity.create(NodeId("peer-a"))
        return TransportGenerationManager(
            identity, store=JsonStateStore(Path(directory) / "transport.json")
        )

    def test_root_identity_survives_restart_and_migrates_old_identity(self) -> None:
        identity = NodeIdentity.create(NodeId("peer-a"))
        restored = NodeIdentity.from_json(
            f'{{"node_id":"peer-a","secret":"{identity.secret}"}}'
        )
        self.assertNotEqual(restored.root_public_key, "")
        self.assertEqual(NodeIdentity.from_json(restored.to_json()), restored)

    def test_offline_rotation_produces_verifiable_continuity(self) -> None:
        identity = NodeIdentity.create(NodeId("peer-a"))
        manager = TransportGenerationManager(identity)
        current = manager.initialize("old")
        next_generation = manager.prepare("new")
        self.assertTrue(
            identity.verify_transport_proof(
                next_generation.generation,
                next_generation.fingerprint,
                next_generation.proof,
            )
        )
        manager.activate(next_generation.generation)
        self.assertTrue(manager.accepted("new"))
        self.assertTrue(manager.accepted(current.fingerprint))

    def test_unrelated_key_claiming_known_node_is_rejected(self) -> None:
        identity = NodeIdentity.create(NodeId("peer-a"))
        manager = TransportGenerationManager(identity)
        manager.initialize("known")
        with self.assertRaises(TransportStateError):
            manager.accept_remote(NodeId("peer-a"), 2, "unrelated", "forged")
        forged = NodeIdentity.create(NodeId("peer-a"))
        proof = forged.sign_transport_proof(2, "unrelated")
        with self.assertRaises(TransportStateError):
            manager.accept_remote(
                NodeId("peer-a"),
                2,
                "unrelated",
                proof,
                root_public_key=identity.root_public_key,
            )

    def test_active_rotation_retirement_and_old_generation_rejection(self) -> None:
        manager = TransportGenerationManager(NodeIdentity.create(NodeId("peer-a")))
        first = manager.initialize("one")
        second = manager.prepare("two")
        manager.activate(second.generation)
        manager.retire(first.generation)
        self.assertFalse(manager.accepted("one"))
        self.assertTrue(manager.accepted("two"))

    def test_racing_rotations_and_rollback_are_explicit(self) -> None:
        manager = TransportGenerationManager(NodeIdentity.create(NodeId("peer-a")))
        manager.initialize("one")
        manager.prepare("two")
        with self.assertRaises(RotationConflict):
            manager.prepare("three")
        manager.rollback()
        self.assertFalse(manager.accepted("two"))

    def test_persistence_failure_does_not_commit_rotation(self) -> None:
        identity = NodeIdentity.create(NodeId("peer-a"))
        saves = 0

        def fail_rotation(_: dict[str, object]) -> bool:
            nonlocal saves
            saves += 1
            return saves == 1

        manager = TransportGenerationManager(identity, persist=fail_rotation)
        manager.initialize("one")
        with self.assertRaises(TransportStateError):
            manager.prepare("two")
        self.assertIsNone(manager.next)

    def test_restart_restores_generations_and_corruption_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            identity = NodeIdentity.create(NodeId("peer-a"))
            path = Path(directory) / "transport.json"
            manager = TransportGenerationManager(identity, store=JsonStateStore(path))
            manager.initialize("one")
            prepared = manager.prepare("two")
            restored = TransportGenerationManager(identity, store=JsonStateStore(path))
            self.assertEqual(restored.next, prepared)
            path.write_text("[]", encoding="utf-8")
            with self.assertRaises(StateDataError):
                TransportGenerationManager(identity, store=JsonStateStore(path))

    def test_old_transport_state_migrates_to_generation_one(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "transport.json"
            JsonStateStore(path).save({"version": 1, "fingerprint": "legacy"})
            manager = TransportGenerationManager(
                NodeIdentity.create(NodeId("peer-a")), store=JsonStateStore(path)
            )
            self.assertEqual(manager.current.fingerprint, "legacy")

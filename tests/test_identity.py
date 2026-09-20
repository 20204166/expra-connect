import json
import tempfile
import unittest
from pathlib import Path

from expra_connect.identity import NodeId, NodeIdentity


class IdentityTests(unittest.TestCase):
    def test_node_id_rejects_empty_and_local_placeholder(self) -> None:
        with self.assertRaises(ValueError):
            NodeId("")
        with self.assertRaises(ValueError):
            NodeId("local")

    def test_node_id_is_value_equal(self) -> None:
        self.assertEqual(NodeId("peer-a"), NodeId("peer-a"))
        self.assertNotEqual(NodeId("peer-a"), NodeId("peer-b"))

    def test_identity_round_trips_atomically(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "identity.json"
            identity = NodeIdentity.create(NodeId("peer-a"))
            identity.save(path)
            restored = NodeIdentity.load(path)
            self.assertEqual(restored.node_id, identity.node_id)
            self.assertEqual(restored.secret, identity.secret)
            self.assertEqual(restored.root_public_key, identity.root_public_key)

    def test_identity_document_is_versioned_json_with_root_key_material(self) -> None:
        identity = NodeIdentity.create(NodeId("peer-a"))
        document = json.loads(identity.to_json())
        self.assertEqual(document["node_id"], "peer-a")
        self.assertEqual(document["version"], 2)
        self.assertTrue(document["root_private_key"])

    def test_identity_rejects_invalid_secret(self) -> None:
        with self.assertRaises(ValueError):
            NodeIdentity(NodeId("peer-a"), "not-a-secret")

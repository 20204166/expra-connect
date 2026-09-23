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

    def test_node_id_rejects_overlong_values(self) -> None:
        with self.assertRaises(ValueError):
            NodeId("x" * 129)

    def test_node_id_rejects_non_string_values(self) -> None:
        with self.assertRaises(ValueError):
            NodeId(["peer-a"])  # type: ignore[arg-type]

    def test_identity_rejects_non_node_id_values(self) -> None:
        with self.assertRaises(ValueError):
            NodeIdentity("peer-a", "0" * 64)  # type: ignore[arg-type]

    def test_identity_rejects_malformed_json_schema_and_field_types(self) -> None:
        secret = "0" * 64
        cases = (
            "[]",
            json.dumps({"version": 99, "node_id": "peer-a", "secret": secret}),
            json.dumps(
                {
                    "version": 1,
                    "schema_version": 2,
                    "node_id": "peer-a",
                    "secret": secret,
                }
            ),
            json.dumps({"node_id": 1, "secret": secret}),
            json.dumps({"node_id": "peer-a", "secret": 1}),
            json.dumps(
                {
                    "node_id": "peer-a",
                    "secret": secret,
                    "root_private_key": "",
                }
            ),
        )
        for value in cases:
            with self.subTest(value=value), self.assertRaises(ValueError):
                NodeIdentity.from_json(value)

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

    def test_identity_rejects_non_string_secret(self) -> None:
        with self.assertRaises(ValueError):
            NodeIdentity(NodeId("peer-a"), None)  # type: ignore[arg-type]

    def test_identity_rejects_invalid_root_key(self) -> None:
        with self.assertRaises(ValueError):
            NodeIdentity(NodeId("peer-a"), "0" * 64, "not-base64")

    def test_identity_repr_and_str_do_not_expose_secrets(self) -> None:
        identity = NodeIdentity.create(NodeId("peer-a"))

        rendered = repr(identity) + str(identity)

        self.assertNotIn(identity.secret, rendered)
        self.assertNotIn(identity.root_private_key, rendered)

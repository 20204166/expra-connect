import base64
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import msgpack

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

    def test_legacy_json_decoder_rejects_split_profile_metadata(self) -> None:
        with self.assertRaisesRegex(ValueError, "schema version is invalid"):
            NodeIdentity.from_json(
                json.dumps(
                    {
                        "schema_version": 3,
                        "node_id": "split-node",
                        "root_public_key": "public",
                        "secret_fingerprint": "a" * 64,
                    }
                )
            )

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

    def test_profile_identity_splits_metadata_json_from_messagepack_secrets(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "identity.json"
            identity = NodeIdentity.create(NodeId("split-profile-node"))

            identity.save(path)

            metadata = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(metadata.get("schema_version"), 3)
            self.assertEqual(metadata.get("node_id"), identity.node_id.value)
            self.assertNotIn("secret", metadata)
            self.assertNotIn("root_private_key", metadata)
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            self.assertEqual(path.with_suffix(".msgpack").stat().st_mode & 0o777, 0o600)
            secrets = msgpack.unpackb(
                path.with_suffix(".msgpack").read_bytes(), raw=False
            )
            self.assertEqual(secrets["secret"], bytes.fromhex(identity.secret))
            self.assertEqual(
                secrets["root_private_key"],
                base64.b64decode(identity.root_private_key, validate=True),
            )
            self.assertEqual(
                NodeIdentity.load(path).root_public_key, identity.root_public_key
            )

    def test_legacy_identity_json_migrates_to_split_profile_files(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "identity.json"
            identity = NodeIdentity.create(NodeId("legacy-split-node"))
            path.write_text(identity.to_json(), encoding="utf-8")

            restored = NodeIdentity.load(path)

            metadata = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(metadata.get("schema_version"), 3)
            self.assertNotIn("secret", metadata)
            self.assertNotIn("root_private_key", metadata)
            secrets = msgpack.unpackb(
                path.with_suffix(".msgpack").read_bytes(), raw=False
            )
            self.assertEqual(restored.node_id, identity.node_id)
            self.assertEqual(restored.root_public_key, identity.root_public_key)
            self.assertEqual(secrets["secret"], bytes.fromhex(identity.secret))

    def test_split_identity_with_missing_secret_bundle_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "identity.json"
            metadata = {
                "schema_version": 3,
                "node_id": "split-node",
                "root_public_key": "public-key",
                "secret_fingerprint": "a" * 64,
            }
            path.write_text(json.dumps(metadata), encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "identity secrets are missing"):
                NodeIdentity.load(path)

    def test_split_identity_metadata_tampering_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "identity.json"
            NodeIdentity.create(NodeId("split-tamper-node")).save(path)
            metadata = json.loads(path.read_text(encoding="utf-8"))
            metadata["secret_fingerprint"] = "0" * 64
            path.write_text(json.dumps(metadata), encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "does not match"):
                NodeIdentity.load(path)

    def test_split_identity_secret_bundle_tampering_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "identity.json"
            identity = NodeIdentity.create(NodeId("split-bundle-tamper"))
            identity.save(path)
            secret_path = path.with_suffix(".msgpack")
            bundle = msgpack.unpackb(secret_path.read_bytes(), raw=False)
            bundle["secret"] = b"x" * 32
            packed = msgpack.packb(bundle, use_bin_type=True)
            assert isinstance(packed, bytes)
            secret_path.write_bytes(packed)

            with self.assertRaisesRegex(ValueError, "does not match"):
                NodeIdentity.load(path)

    def test_failed_legacy_split_keeps_the_legacy_identity_readable(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "identity.json"
            identity = NodeIdentity.create(NodeId("migration-commit-node"))
            legacy_document = identity.to_json()
            path.write_text(legacy_document, encoding="utf-8")

            with (
                patch("expra_connect.identity._atomic_write", side_effect=OSError),
                self.assertRaises(OSError),
            ):
                NodeIdentity.load(path)

            self.assertEqual(path.read_text(encoding="utf-8"), legacy_document)

    def test_identity_document_is_versioned_json_with_root_key_material(self) -> None:
        identity = NodeIdentity.create(NodeId("peer-a"))
        document = json.loads(identity.to_json())
        self.assertEqual(document["node_id"], "peer-a")
        self.assertEqual(document["version"], 2)
        self.assertTrue(document["root_private_key"])

    def test_identity_json_round_trip_preserves_unknown_extension_fields(self) -> None:
        document = json.loads(NodeIdentity.create(NodeId("peer-a")).to_json())
        document["future_extension"] = {"issuer": "newer-runtime", "epoch": 3}

        restored = NodeIdentity.from_json(json.dumps(document))
        round_tripped = json.loads(restored.to_json())

        self.assertEqual(
            round_tripped.get("future_extension"),
            {"issuer": "newer-runtime", "epoch": 3},
        )

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

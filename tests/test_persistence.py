import os
import tempfile
import unittest
from pathlib import Path
from typing import NoReturn
from unittest.mock import patch

from expra_connect.persistence import (
    JsonStateStore,
    MessagePackStateStore,
    StateDataError,
    YamlStateStore,
    migrate_state,
)
from expra_connect.runtime.persistence import peer_grant_from_json


class PersistenceTests(unittest.TestCase):
    def test_messagepack_state_rejects_malformed_and_oversized_files(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "identity.msgpack"
            store = MessagePackStateStore(path)

            path.write_bytes(b"\xc1")
            with self.assertRaises(StateDataError):
                store.load()

            path.write_bytes(b"x" * (store.MAX_BYTES + 1))
            with self.assertRaisesRegex(StateDataError, "size limit"):
                store.load()

    def test_messagepack_state_rejects_non_string_root_keys(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = MessagePackStateStore(Path(directory) / "identity.msgpack")

            with self.assertRaises(StateDataError):
                store.save({1: "invalid"})  # type: ignore[dict-item]

    def test_persisted_secret_must_decode_to_256_bits(self) -> None:
        with self.assertRaises(ValueError):
            peer_grant_from_json(
                {
                    "caller_id": "peer",
                    "secret": "a" * 62 + "  ",
                    "permissions": [],
                }
            )

    def test_legacy_trust_and_single_route_records_migrate_without_secrets_in_output(
        self,
    ) -> None:
        document = migrate_state(
            "trust",
            {"grants": [{"caller_id": "peer", "host": "10.0.0.2", "port": 27321}]},
        )
        self.assertEqual(document["schema_version"], 2)
        self.assertEqual(document["grants"][0]["routes"][0]["source"], "legacy")
        self.assertNotIn("secret", document["grants"][0])

    def test_unknown_schema_and_malformed_route_state_fail_closed(self) -> None:
        with self.assertRaises(StateDataError):
            migrate_state("trust", {"schema_version": 99})
        with self.assertRaises(StateDataError):
            migrate_state("routes", {"routes": {"host": "bad"}})

    def test_json_state_round_trips_with_private_file_mode(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "state.json"
            store = JsonStateStore(path)
            store.save({"version": 1, "items": ["peer"]})
            self.assertEqual(store.load()["items"], ["peer"])
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)

    def test_json_state_save_is_sorted(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "state.json"
            JsonStateStore(path).save({"z": 1, "a": 2})
            self.assertEqual(path.read_text(encoding="utf-8"), '{"a": 2, "z": 1}')

    def test_corrupt_or_non_object_state_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "state.json"
            path.write_text("not json", encoding="utf-8")
            with self.assertRaises(StateDataError):
                JsonStateStore(path).load()
            path.write_text("[]", encoding="utf-8")
            with self.assertRaises(StateDataError):
                JsonStateStore(path).load()

    def test_directory_fsync_is_best_effort_for_windows_filesystems(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "state.json"
            real_fsync = os.fsync
            calls = 0

            def fsync_once(file_descriptor: int) -> None:
                nonlocal calls
                calls += 1
                if calls == 2:
                    raise PermissionError
                real_fsync(file_descriptor)

            with patch("expra_connect.persistence.os.fsync", side_effect=fsync_once):
                JsonStateStore(path).save({"version": 1})
            self.assertEqual(JsonStateStore(path).load()["version"], 1)

    def test_failed_descriptor_open_does_not_leak_the_raw_descriptor(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "state.json"
            captured: list[int] = []

            def failing_fdopen(
                file_descriptor: int, *_args: object, **_kwargs: object
            ) -> NoReturn:
                captured.append(file_descriptor)
                raise OSError("descriptor open failed")

            with (
                patch(
                    "expra_connect.persistence.os.fdopen",
                    side_effect=failing_fdopen,
                ),
                self.assertRaises(OSError),
            ):
                JsonStateStore(path).save({"version": 1})

            self.assertEqual(len(captured), 1)
            with self.assertRaises(OSError):
                os.fstat(captured[0])


class YamlStateStoreTests(unittest.TestCase):
    def test_exists_includes_legacy_json_source(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "state.yaml"
            store = YamlStateStore(path)
            self.assertFalse(store.exists())
            path.with_suffix(".json").write_text("{}", encoding="utf-8")
            self.assertTrue(store.exists())

    def test_round_trip_preserves_content_and_uses_private_file_mode(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "state.yaml"
            store = YamlStateStore(path)
            store.save({"version": 1, "items": ["peer-a", "peer-b"], "count": 2})
            loaded = store.load()
            self.assertEqual(loaded["items"], ["peer-a", "peer-b"])
            self.assertEqual(loaded["count"], 2)
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)

    def test_auto_migrate_reads_json_writes_yaml_and_preserves_json(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            json_path = Path(directory) / "state.json"
            yaml_path = Path(directory) / "state.yaml"
            json_path.write_text('{"version": 1, "peer": "abc"}', encoding="utf-8")
            store = YamlStateStore(yaml_path)
            result = store.load()
            self.assertEqual(result["peer"], "abc")
            self.assertTrue(yaml_path.exists(), "yaml not created after migration")
            self.assertTrue(json_path.exists(), "legacy JSON was deleted")

    def test_auto_migrate_prefers_yaml_when_both_files_present(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            json_path = Path(directory) / "state.json"
            yaml_path = Path(directory) / "state.yaml"
            json_path.write_text('{"version": 1, "source": "json"}', encoding="utf-8")
            yaml_path.write_text("version: 1\nsource: yaml\n", encoding="utf-8")
            result = YamlStateStore(yaml_path).load()
            self.assertEqual(result["source"], "yaml")
            self.assertTrue(
                json_path.exists(), "json removed when yaml already present"
            )

    def test_auto_migrate_neither_file_present_raises_state_data_error(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            yaml_path = Path(directory) / "state.yaml"
            with self.assertRaises(StateDataError):
                YamlStateStore(yaml_path).load()

    def test_malformed_yaml_raises_state_data_error(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "state.yaml"
            path.write_text("key: [unclosed\n", encoding="utf-8")
            with self.assertRaises(StateDataError):
                YamlStateStore(path).load()

    def test_empty_file_raises_state_data_error(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "state.yaml"
            path.write_text("", encoding="utf-8")
            with self.assertRaises(StateDataError):
                YamlStateStore(path).load()

    def test_non_dict_root_raises_state_data_error(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "state.yaml"
            path.write_text("- item1\n- item2\n", encoding="utf-8")
            with self.assertRaises(StateDataError):
                YamlStateStore(path).load()

    def test_null_root_raises_state_data_error(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "state.yaml"
            path.write_text("null\n", encoding="utf-8")
            with self.assertRaises(StateDataError):
                YamlStateStore(path).load()

    def test_corrupt_json_source_during_migration_raises_and_leaves_json_intact(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            json_path = Path(directory) / "state.json"
            yaml_path = Path(directory) / "state.yaml"
            json_path.write_text("not valid json {{{", encoding="utf-8")
            with self.assertRaises(StateDataError):
                YamlStateStore(yaml_path).load()
            self.assertTrue(json_path.exists(), "json removed despite failed migration")
            self.assertFalse(
                yaml_path.exists(), "yaml created despite failed migration"
            )

    def test_string_values_survive_round_trip_without_boolean_coercion(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "state.yaml"
            store = YamlStateStore(path)
            data = {"flag": "true", "other": "false", "yes_key": "yes", "no_key": "no"}
            store.save(data)
            loaded = store.load()
            for key, value in data.items():
                with self.subTest(key=key):
                    self.assertIsInstance(loaded[key], str)
                    self.assertEqual(loaded[key], value)

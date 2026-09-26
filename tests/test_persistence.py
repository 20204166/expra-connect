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
    migrate_state,
)
from expra_connect.runtime_persistence import peer_grant_from_json


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
                file_descriptor: int, *args: object, **kwargs: object
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

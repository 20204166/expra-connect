import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from expra_connect.persistence import JsonStateStore, StateDataError


class PersistenceTests(unittest.TestCase):
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

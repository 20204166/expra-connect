import tempfile
import unittest
from pathlib import Path

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

import json
import tempfile
import unittest
from pathlib import Path

import msgpack

from expra_connect.persistence import JsonStateStore
from scripts import benchmark_persistence


class PersistenceBenchmarkTests(unittest.TestCase):
    def test_json_fixtures_preserve_each_benchmark_shape(self) -> None:
        dictionary_records, dictionary_shape = benchmark_persistence._json_fixture(
            2, "dictionary_records"
        )
        compact_pairs, compact_shape = benchmark_persistence._json_fixture(
            2, "compact_pairs"
        )

        self.assertEqual(dictionary_shape, "dictionary_records")
        self.assertEqual(
            dictionary_records,
            {
                "version": 2,
                "peers": [
                    {"id": "node-0", "online": True},
                    {"id": "node-1", "online": False},
                ],
            },
        )
        self.assertEqual(compact_shape, "compact_pairs")
        self.assertEqual(
            compact_pairs, {"version": 2, "peers": [[0, True], [1, False]]}
        )

    def test_json_fixture_rejects_invalid_sizes_and_shapes(self) -> None:
        with self.assertRaises(ValueError):
            benchmark_persistence._json_fixture(0, "compact_pairs")
        with self.assertRaises(ValueError):
            benchmark_persistence._json_fixture(1, "unknown")

    def test_json_root_stage_matches_store_object_root_contract(self) -> None:
        self.assertTrue(benchmark_persistence._has_object_root({"records": []}))
        self.assertFalse(benchmark_persistence._has_object_root([]))
        self.assertFalse(benchmark_persistence._has_object_root(None))

    def test_json_decode_stage_matches_store_load(self) -> None:
        payload, _shape = benchmark_persistence._json_fixture(2, "compact_pairs")
        encoded = json.dumps(payload)

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "state.json"
            path.write_text(encoded, encoding="utf-8")

            self.assertEqual(json.loads(encoded), JsonStateStore(path).load())

    def test_messagepack_stages_decode_store_format_and_identify_invalid_roots(
        self,
    ) -> None:
        payload = {"records": [[1, True], [2, False]]}
        encoded_result = msgpack.packb(payload, use_bin_type=True)
        if not isinstance(encoded_result, bytes):
            self.fail("MessagePack encoder returned invalid data")
        encoded = encoded_result

        decoded = benchmark_persistence._decode_messagepack(encoded)

        self.assertEqual(decoded, payload)
        self.assertTrue(benchmark_persistence._has_string_keyed_root(decoded))
        self.assertFalse(
            benchmark_persistence._has_string_keyed_root({1: "not a string key"})
        )
        self.assertFalse(
            benchmark_persistence._has_string_keyed_root(["not", "a", "map"])
        )


if __name__ == "__main__":
    unittest.main()

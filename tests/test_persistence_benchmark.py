import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import msgpack

from expra_connect.persistence import JsonStateStore
from scripts import benchmark_persistence

ROOT = Path(__file__).resolve().parents[1]


class PersistenceBenchmarkTests(unittest.TestCase):
    def test_migration_fixture_and_option_selection(self) -> None:
        fixture = benchmark_persistence._migration_fixture(2)
        self.assertEqual(
            fixture,
            {"state-000.json": b"x" * 256, "state-001.json": b"x" * 256},
        )
        for invalid_count in (0, -1, True):
            with (
                self.subTest(invalid_count=invalid_count),
                self.assertRaises(ValueError),
            ):
                benchmark_persistence._migration_fixture(invalid_count)

        self.assertEqual(
            benchmark_persistence._resolve_migration_counts(
                "Profile migration", None, None
            ),
            (1, 16, 64),
        )
        with self.assertRaises(ValueError):
            benchmark_persistence._resolve_migration_counts(
                "JSON store load", None, (1,)
            )
        with self.assertRaises(ValueError):
            benchmark_persistence._resolve_migration_counts(
                "Profile migration", (30_000,), None
            )

    def test_profile_migration_benchmark_cli(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output_path = Path(directory) / "migration.json"
            completed = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "scripts.benchmark_persistence",
                    "--migration-file-counts",
                    "1",
                    "--operation",
                    "Profile migration",
                    "--warmups",
                    "0",
                    "--repetitions",
                    "1",
                    "--json-output",
                    str(output_path),
                ],
                cwd=ROOT,
                capture_output=True,
                text=True,
                check=False,
            )

            self.assertEqual(completed.returncode, 0, completed.stderr)
            result = json.loads(output_path.read_text(encoding="utf-8"))

        self.assertEqual(result["metadata"]["suite"], "persistence")
        self.assertEqual(len(result["outcomes"]), 1)
        outcome = result["outcomes"][0]
        self.assertEqual(outcome["operation"], "Profile migration")
        self.assertEqual(outcome["size"], 1)
        self.assertGreater(outcome["payload_bytes"], 0)
        self.assertEqual(outcome["fixture"]["shape"], "flat_files")
        self.assertIn("p50=", completed.stdout)

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

    def test_module_cli_selects_json_store_save_and_emits_shared_result(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output_path = Path(directory) / "persistence.json"
            completed = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "scripts.benchmark_persistence",
                    "--sizes",
                    "30000",
                    "--operation",
                    "JSON store save",
                    "--warmups",
                    "0",
                    "--repetitions",
                    "1",
                    "--json-output",
                    str(output_path),
                ],
                cwd=ROOT,
                capture_output=True,
                text=True,
                check=False,
            )

            self.assertEqual(completed.returncode, 0, completed.stderr)
            result = json.loads(output_path.read_text(encoding="utf-8"))

        self.assertEqual(result["metadata"]["suite"], "persistence")
        self.assertEqual(len(result["outcomes"]), 1)
        outcome = result["outcomes"][0]
        self.assertEqual(outcome["operation"], "JSON store save")
        self.assertEqual(outcome["fixture"]["shape"], "compact_pairs")
        self.assertEqual(outcome["size"], 30_000)
        self.assertIn("p50=", completed.stdout)

    def test_100k_compact_fixture_keeps_messagepack_and_json_comparable(self) -> None:
        package_root = Path("/tmp/opencode/bench-comparison-2.2.0.1/1.8.0.0")
        environment = os.environ.copy()
        environment["PYTHONPATH"] = str(package_root)
        with tempfile.TemporaryDirectory() as directory:
            output_path = Path(directory) / "persistence-100k.json"
            completed = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "scripts.benchmark_persistence",
                    "--sizes",
                    "100000",
                    "--warmups",
                    "0",
                    "--repetitions",
                    "1",
                    "--json-output",
                    str(output_path),
                ],
                cwd=ROOT,
                env=environment,
                capture_output=True,
                text=True,
                check=False,
                timeout=30,
            )

            self.assertEqual(completed.returncode, 0, completed.stderr)
            result = json.loads(output_path.read_text(encoding="utf-8"))

        messagepack = [
            outcome
            for outcome in result["outcomes"]
            if outcome["operation"].startswith("MessagePack")
        ]
        json_store = [
            outcome
            for outcome in result["outcomes"]
            if outcome["operation"] == "JSON store save"
        ]
        self.assertTrue(messagepack)
        self.assertTrue(all(item["status"] == "measured" for item in messagepack))
        self.assertEqual(len(json_store), 1)
        self.assertEqual(json_store[0]["status"], "measured")


if __name__ == "__main__":
    unittest.main()

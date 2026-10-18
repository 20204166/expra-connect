import unittest

import msgpack

from scripts.benchmark_persistence import (
    _decode_messagepack,
    _has_string_keyed_root,
)


class PersistenceBenchmarkTests(unittest.TestCase):
    def test_messagepack_stages_decode_store_format_and_identify_invalid_roots(
        self,
    ) -> None:
        payload = {"records": [[1, True], [2, False]]}
        encoded_result = msgpack.packb(payload, use_bin_type=True)
        if not isinstance(encoded_result, bytes):
            self.fail("MessagePack encoder returned invalid data")
        encoded = encoded_result

        decoded = _decode_messagepack(encoded)

        self.assertEqual(decoded, payload)
        self.assertTrue(_has_string_keyed_root(decoded))
        self.assertFalse(_has_string_keyed_root({1: "not a string key"}))
        self.assertFalse(_has_string_keyed_root(["not", "a", "map"]))


if __name__ == "__main__":
    unittest.main()

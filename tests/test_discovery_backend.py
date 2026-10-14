"""Tests for shared Zeroconf service-address decoding."""

import unittest
from types import SimpleNamespace
from typing import Any

from expra_connect.discovery.backend import (
    _address_text,
    _address_texts,
    _service_address_texts,
)


def _info(**attrs: Any) -> Any:
    return SimpleNamespace(**attrs)


class AddressDecodingTests(unittest.TestCase):
    def test_packed_ipv4_bytes(self) -> None:
        self.assertEqual(_address_text(b"\xc0\xa8\x01\x02"), "192.168.1.2")

    def test_packed_ipv6_bytes(self) -> None:
        packed = bytes.fromhex("20010db8000000000000000000000001")
        self.assertEqual(_address_text(packed), "2001:db8::1")

    def test_malformed_packed_lengths_are_skipped(self) -> None:
        for raw in (b"", b"\x01\x02\x03", b"\x01" * 5, b"\x01" * 15, b"\x01" * 17):
            self.assertIsNone(_address_text(raw), raw)

    def test_arbitrary_object_is_not_stringified(self) -> None:
        self.assertIsNone(_address_text(object()))
        self.assertIsNone(_address_text(1234))

    def test_string_addresses_are_preserved(self) -> None:
        self.assertEqual(_address_text("2001:db8::9"), "2001:db8::9")
        self.assertIsNone(_address_text("   "))

    def test_malformed_item_does_not_kill_valid_siblings(self) -> None:
        texts = _address_texts([b"\x01\x02\x03", b"\xc0\xa8\x01\x02"])
        self.assertEqual(texts, ["192.168.1.2"])

    def test_duplicate_addresses_collapse_preserving_order(self) -> None:
        texts = _address_texts(
            [b"\xc0\xa8\x01\x02", b"\xc0\xa8\x01\x02", b"\xc0\xa8\x01\x03"]
        )
        self.assertEqual(texts, ["192.168.1.2", "192.168.1.3"])

    def test_empty_and_scalar_addresses_are_safe(self) -> None:
        self.assertEqual(_address_texts([]), [])
        self.assertEqual(_address_texts(()), [])
        self.assertEqual(_address_texts(123), [])
        self.assertEqual(_address_texts(None), [])

    def test_parsed_strings_are_preserved_in_order(self) -> None:
        info = _info(
            addresses=[],
            parsed_addresses=lambda: ["192.168.1.2", "2001:db8::1"],
        )
        self.assertEqual(_service_address_texts(info), ["192.168.1.2", "2001:db8::1"])

    def test_scoped_ipv6_scope_is_preserved(self) -> None:
        info = _info(parsed_scoped_addresses=lambda: ["fe80::1234%3"])
        self.assertEqual(_service_address_texts(info), ["fe80::1234%3"])

    def test_scoped_api_is_preferred_over_parsed(self) -> None:
        info = _info(
            parsed_scoped_addresses=lambda: ["fe80::1234%3"],
            parsed_addresses=lambda: ["fe80::1234"],
        )
        self.assertEqual(_service_address_texts(info), ["fe80::1234%3"])

    def test_parsed_api_is_preferred_over_raw(self) -> None:
        info = _info(
            addresses=[b"\xc0\xa8\x01\x02"],
            parsed_addresses=lambda: ["2001:db8::9"],
        )
        self.assertEqual(_service_address_texts(info), ["2001:db8::9"])

    def test_raw_fallback_is_strict_when_parsed_api_absent(self) -> None:
        info = _info(addresses=[b"\x01\x02\x03", b"\xc0\xa8\x01\x02"])
        self.assertEqual(_service_address_texts(info), ["192.168.1.2"])

    def test_parsed_api_returning_empty_falls_back_to_raw(self) -> None:
        info = _info(
            addresses=[b"\xc0\xa8\x01\x02"],
            parsed_addresses=list,
        )
        self.assertEqual(_service_address_texts(info), ["192.168.1.2"])

    def test_parsed_api_exception_falls_back_to_raw(self) -> None:
        def _explode() -> list[str]:
            raise RuntimeError("parsed failed")

        info = _info(addresses=[b"\xc0\xa8\x01\x02"], parsed_addresses=_explode)
        self.assertEqual(_service_address_texts(info), ["192.168.1.2"])

    def test_empty_raw_addresses_return_empty(self) -> None:
        self.assertEqual(_service_address_texts(_info(addresses=[], port=1)), [])


if __name__ == "__main__":
    unittest.main()

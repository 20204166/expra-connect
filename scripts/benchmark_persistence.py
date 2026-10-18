"""Attribute MessagePack state-store load cost with matched benchmark stages."""

from __future__ import annotations

import argparse
import importlib
import json
import platform
import statistics
import sys
import tempfile
import time
from collections.abc import Callable
from functools import partial
from pathlib import Path
from typing import Any

import msgpack

from expra_connect.persistence import JsonStateStore, MessagePackStateStore


def _messagepack_options() -> dict[str, Any]:
    """Return the exact decoder limits used by ``MessagePackStateStore.load``."""

    return {
        "raw": False,
        "strict_map_key": True,
        "max_str_len": MessagePackStateStore.MAX_BYTES,
        "max_bin_len": MessagePackStateStore.MAX_BYTES,
        "max_array_len": 100_000,
        "max_map_len": 100_000,
        "max_ext_len": 0,
    }


def _decode_messagepack(payload: bytes) -> Any:
    return msgpack.unpackb(payload, **_messagepack_options())


def _has_string_keyed_root(value: object) -> bool:
    return isinstance(value, dict) and all(isinstance(key, str) for key in value)


def _has_object_root(value: object) -> bool:
    return isinstance(value, dict)


def _json_fixture(size: int, shape: str) -> tuple[dict[str, Any], str]:
    if size < 1:
        raise ValueError("JSON fixture size must be positive")
    if shape == "dictionary_records":
        peers: list[object] = [
            {"id": f"node-{index}", "online": index % 2 == 0} for index in range(size)
        ]
    elif shape == "compact_pairs":
        peers = [[index, index % 2 == 0] for index in range(size)]
    else:
        raise ValueError(f"unknown JSON fixture shape: {shape}")
    return {"version": 2, "peers": peers}, shape


def _fixture(size: int) -> tuple[dict[str, Any], str]:
    if size == 30_000:
        return (
            {
                "version": 2,
                "peers": [
                    {"id": f"node-{index}", "online": index % 2 == 0}
                    for index in range(size)
                ],
            },
            "dictionary_records",
        )
    if size == 100_000:
        return (
            {
                "version": 2,
                "peers": [[index, index % 2 == 0] for index in range(size)],
            },
            "compact_pairs",
        )
    raise ValueError(f"unsupported benchmark size: {size}")


def _measure(
    operation: Callable[[], Any], *, warmups: int, repetitions: int
) -> tuple[float, float, float]:
    for _ in range(warmups):
        operation()
    samples: list[float] = []
    for _ in range(repetitions):
        started = time.perf_counter_ns()
        operation()
        samples.append((time.perf_counter_ns() - started) / 1_000)
    return statistics.median(samples), min(samples), max(samples)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--sizes", type=int, nargs="+", choices=(30_000, 100_000, 200_000)
    )
    parser.add_argument("--warmups", type=int, default=2)
    parser.add_argument("--repetitions", type=int, default=9)
    args = parser.parse_args()
    if args.warmups < 0 or args.repetitions < 1:
        parser.error("warmups must be >= 0 and repetitions must be >= 1")

    persistence = importlib.import_module("expra_connect.persistence")
    print(f"Python: {sys.version.split()[0]} ({platform.platform()})")
    print(f"persistence module: {persistence.__file__}")
    print(f"json module: {json.__file__}")
    print(f"msgpack module: {msgpack.__file__}")
    print(
        f"Warmups: {args.warmups}; repetitions: {args.repetitions}; "
        "read timings use the OS page cache after warmup"
    )

    requested_sizes = args.sizes or (30_000, 100_000)
    for size in (value for value in requested_sizes if value in (30_000, 100_000)):
        value, shape = _fixture(size)
        encoded_result = msgpack.packb(value, use_bin_type=True)
        if not isinstance(encoded_result, bytes):
            raise TypeError("MessagePack encoder returned invalid data")
        encoded: bytes = encoded_result
        if len(encoded) > MessagePackStateStore.MAX_BYTES:
            parser.error(f"fixture exceeds MessagePack limit: {len(encoded)} bytes")
        decoded = _decode_messagepack(encoded)
        if not _has_string_keyed_root(decoded):
            raise RuntimeError("benchmark fixture has an invalid root")

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "state.msgpack"
            path.write_bytes(encoded)
            msgpack_store = MessagePackStateStore(path)

            def decode_and_validate(payload: bytes = encoded) -> Any:
                result = _decode_messagepack(payload)
                if not _has_string_keyed_root(result):
                    raise ValueError("MessagePack root is not a string-keyed map")
                return result

            operations: tuple[tuple[str, Callable[[], Any]], ...] = (
                ("file read", path.read_bytes),
                (
                    "MessagePack decode",
                    partial(_decode_messagepack, encoded),
                ),
                (
                    "root validation",
                    partial(_has_string_keyed_root, decoded),
                ),
                ("decode + root validation", decode_and_validate),
                ("store load", msgpack_store.load),
            )
            print(
                f"\nEntries: {size:,}; shape: {shape}; "
                f"payload: {len(encoded):,} bytes; "
                f"store limit: {MessagePackStateStore.MAX_BYTES:,} bytes"
            )
            for label, operation in operations:
                median, low, high = _measure(
                    operation, warmups=args.warmups, repetitions=args.repetitions
                )
                print(f"{label}: p50={median:,.1f} us range={low:,.1f}..{high:,.1f} us")

    for size in requested_sizes:
        value, shape = _json_fixture(size, "compact_pairs")
        encoded_text = json.dumps(value)
        encoded_bytes = encoded_text.encode("utf-8")
        decoded = json.loads(encoded_text)
        if not _has_object_root(decoded):
            raise RuntimeError("JSON benchmark fixture has an invalid root")

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "state.json"
            path.write_text(encoded_text, encoding="utf-8")
            json_store = JsonStateStore(path)
            json_operations: tuple[tuple[str, Callable[[], Any]], ...] = (
                ("JSON file read", partial(path.read_text, encoding="utf-8")),
                ("JSON decode", partial(json.loads, encoded_text)),
                ("object-root validation", partial(_has_object_root, decoded)),
                (
                    "full JSON store load",
                    json_store.load,
                ),
            )
            print(
                f"\nJSON entries: {size:,}; shape: {shape}; "
                f"payload: {len(encoded_bytes):,} bytes"
            )
            for label, operation in json_operations:
                median, low, high = _measure(
                    operation, warmups=args.warmups, repetitions=args.repetitions
                )
                print(f"{label}: p50={median:,.1f} us range={low:,.1f}..{high:,.1f} us")


if __name__ == "__main__":
    main()

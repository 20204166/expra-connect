"""Attribute MessagePack state-store load cost with matched benchmark stages."""

from __future__ import annotations

import argparse
import json
import platform
import sys
import tempfile
from collections.abc import Callable, Sequence
from functools import partial
from pathlib import Path
from typing import Any

import msgpack

from expra_connect import __version__
from expra_connect.persistence import JsonStateStore, MessagePackStateStore
from scripts.benchmark_support import (
    BenchmarkOutcome,
    MeasurementConfig,
    RunMetadata,
    collect_modules,
    format_result,
    measure,
    write_results,
)


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


def _run_operations(
    operations: Sequence[tuple[str, Callable[[], Any]]],
    *,
    selected: str | None,
    suite_prefix: str,
    fixture: dict[str, Any],
    size: int,
    payload_bytes: int,
    config: MeasurementConfig,
) -> list[BenchmarkOutcome]:
    if selected is None:
        selected_operations = operations
    else:
        selected_operations = [item for item in operations if item[0] == selected]
        if not selected_operations:
            return []
    outcomes: list[BenchmarkOutcome] = []
    for label, operation in selected_operations:
        result = measure(operation, config)
        outcomes.append(
            BenchmarkOutcome(
                case_id=f"{size}:{suite_prefix}:{label}",
                operation=label,
                fixture=fixture,
                size=size,
                payload_bytes=payload_bytes,
                config=config,
                result=result,
            )
        )
        print(f"{label}: {format_result(result)}")
    return outcomes


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--sizes", type=int, nargs="+", choices=(30_000, 100_000, 200_000)
    )
    parser.add_argument("--warmups", type=int, default=2)
    parser.add_argument("--repetitions", type=int, default=9)
    parser.add_argument("--batch-count", type=int, default=1)
    parser.add_argument("--track-allocations", action="store_true")
    parser.add_argument(
        "--operation",
        choices=(
            "MessagePack encode",
            "MessagePack file read",
            "MessagePack decode",
            "MessagePack root validation",
            "MessagePack decode + root validation",
            "MessagePack store load",
            "MessagePack store save",
            "JSON encode",
            "JSON file read",
            "JSON decode",
            "JSON object-root validation",
            "JSON store load",
            "JSON store save",
        ),
    )
    parser.add_argument("--json-output", type=Path)
    args = parser.parse_args()
    try:
        config = MeasurementConfig(
            warmups=args.warmups,
            repetitions=args.repetitions,
            batch_count=args.batch_count,
            track_allocations=args.track_allocations,
        )
    except (TypeError, ValueError) as error:
        parser.error(str(error))
    requested_sizes = args.sizes or (200_000,)
    if (
        200_000 in requested_sizes
        and args.operation is not None
        and args.operation.startswith("MessagePack")
        and args.operation not in ("MessagePack store load", "MessagePack store save")
    ):
        parser.error("200k MessagePack codec-only measurements are out of scope")

    modules = collect_modules(("expra_connect.persistence", "msgpack", "json"))
    for module_name, module_path in modules.items():
        print(f"module={module_name} path={module_path}")
    print(
        f"version={__version__} "
        f"python={sys.version.split()[0]} platform={platform.platform()}"
    )
    outcomes: list[BenchmarkOutcome] = []
    for size in requested_sizes:
        if size == 200_000:
            if args.operation is None or args.operation in (
                "MessagePack store load",
                "MessagePack store save",
            ):
                fixture = {"shape": "compact_pairs"}
                reason = (
                    "Unsupported by payload and array limits; codec-only "
                    "measurements are out of scope"
                )
                unsupported_operations = (
                    (args.operation,)
                    if args.operation is not None
                    else ("MessagePack store load", "MessagePack store save")
                )
                for label in unsupported_operations:
                    outcomes.append(
                        BenchmarkOutcome(
                            case_id=f"{size}:MessagePack:{label}",
                            operation=label,
                            fixture=fixture,
                            size=size,
                            payload_bytes=None,
                            config=config,
                            unavailable_reason=reason,
                        )
                    )
                    print(f"{label}: unavailable ({reason})")
            continue

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

            fixture = {"shape": shape, "codec": "msgpack"}
            print(
                f"\nMessagePack entries: {size:,}; shape: {shape}; "
                f"payload: {len(encoded):,} bytes; "
                f"store limit: {MessagePackStateStore.MAX_BYTES:,} bytes"
            )
            outcomes.extend(
                _run_operations(
                    (
                        (
                            "MessagePack encode",
                            partial(msgpack.packb, value, use_bin_type=True),
                        ),
                        ("MessagePack file read", path.read_bytes),
                        ("MessagePack decode", partial(_decode_messagepack, encoded)),
                        (
                            "MessagePack root validation",
                            partial(_has_string_keyed_root, decoded),
                        ),
                        ("MessagePack decode + root validation", decode_and_validate),
                        ("MessagePack store load", msgpack_store.load),
                        (
                            "MessagePack store save",
                            partial(msgpack_store.save, value),
                        ),
                    ),
                    selected=args.operation,
                    suite_prefix="MessagePack",
                    fixture=fixture,
                    size=size,
                    payload_bytes=len(encoded),
                    config=config,
                )
            )

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
            fixture = {"shape": shape, "codec": "json"}
            print(
                f"\nJSON entries: {size:,}; shape: {shape}; "
                f"payload: {len(encoded_bytes):,} bytes"
            )
            outcomes.extend(
                _run_operations(
                    (
                        ("JSON encode", partial(json.dumps, value)),
                        ("JSON file read", partial(path.read_text, encoding="utf-8")),
                        ("JSON decode", partial(json.loads, encoded_text)),
                        (
                            "JSON object-root validation",
                            partial(_has_object_root, decoded),
                        ),
                        ("JSON store load", json_store.load),
                        ("JSON store save", partial(json_store.save, value)),
                    ),
                    selected=args.operation,
                    suite_prefix="JSON",
                    fixture=fixture,
                    size=size,
                    payload_bytes=len(encoded_bytes),
                    config=config,
                )
            )

    if args.json_output is not None:
        write_results(
            args.json_output,
            RunMetadata(
                schema_version=1,
                suite="persistence",
                package_version=__version__,
                python_version=sys.version.split()[0],
                platform=platform.platform(),
                modules=modules,
                config=config,
            ),
            outcomes,
        )


if __name__ == "__main__":
    main()

"""Small atomic JSON/YAML persistence boundary for package-owned state."""

from __future__ import annotations

import copy
import json
import os
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import Any, BinaryIO, TextIO, cast

import msgpack
import yaml


class StateDataError(ValueError):
    """Raised when persisted state is structurally invalid or unreadable."""


CURRENT_SCHEMA_VERSION = 2
_YAML_DUMPER = getattr(yaml, "CSafeDumper", yaml.SafeDumper)
_YAML_LOADER = getattr(yaml, "CSafeLoader", yaml.SafeLoader)


def _yaml_dump(value: Any, stream: TextIO | None = None) -> str | None:
    """Serialize with the LibYAML safe dumper when available."""
    return cast(
        str | None,
        yaml.dump(
            value,
            stream=stream,
            Dumper=_YAML_DUMPER,
            sort_keys=True,
            allow_unicode=True,
        ),
    )


def _yaml_load(value: str) -> Any:
    """Parse with the LibYAML safe loader when available."""
    return yaml.load(value, Loader=_YAML_LOADER)


class _NonJsonTreeError(Exception):
    """Signal that a state document needs general ``deepcopy`` semantics."""


def _clone_json_tree(value: Any, memo: dict[int, Any]) -> Any:
    """Clone exact JSON-shaped containers while preserving aliases and cycles."""
    value_type = type(value)
    if value_type in (str, int, float, bool, type(None)):
        return value
    if value_type is list:
        identity = id(value)
        if identity in memo:
            return memo[identity]
        cloned: list[Any] = []
        memo[identity] = cloned
        cloned.extend(_clone_json_tree(item, memo) for item in value)
        return cloned
    if value_type is dict:
        identity = id(value)
        if identity in memo:
            return memo[identity]
        cloned_dict: dict[str, Any] = {}
        memo[identity] = cloned_dict
        for key, item in value.items():
            if type(key) is not str:
                raise _NonJsonTreeError
            cloned_dict[key] = _clone_json_tree(item, memo)
        return cloned_dict
    raise _NonJsonTreeError


def _clone_state(value: dict[str, Any]) -> dict[str, Any]:
    """Use a focused fast path for JSON trees; preserve deepcopy otherwise."""
    try:
        return cast(dict[str, Any], _clone_json_tree(value, {}))
    except _NonJsonTreeError:
        return copy.deepcopy(value)


def _atomic_write(
    path: Path,
    writer: Callable[[TextIO], object],
    *,
    mode: int = 0o600,
    sync_directory: bool = True,
) -> None:
    """Write a private document through a flushed, replace-on-success file."""

    _atomic_write_stream(
        path,
        cast(Callable[[Any], object], writer),
        binary=False,
        mode=mode,
        sync_directory=sync_directory,
    )


def _save_text_document(
    path: Path,
    value: dict[str, Any],
    *,
    atomic_write: Callable[..., None] = _atomic_write,
    sync_directory: bool = True,
) -> None:
    """Write YAML documents, retaining JSON encoding for legacy ``.json`` paths."""
    if path.suffix == ".json":
        _save_json_document(
            path,
            value,
            atomic_write=atomic_write,
            sync_directory=sync_directory,
        )
    else:
        atomic_write(
            path, lambda file: _yaml_dump(value, file), sync_directory=sync_directory
        )


def _save_json_document(
    path: Path,
    value: dict[str, Any],
    *,
    atomic_write: Callable[..., None] = _atomic_write,
    sync_directory: bool = True,
) -> None:
    """Write deterministic JSON through the configured atomic boundary."""
    atomic_write(
        path,
        lambda file: file.write(json.dumps(value, sort_keys=True)),
        sync_directory=sync_directory,
    )


def _atomic_write_bytes(
    path: Path,
    payload: bytes,
    *,
    mode: int = 0o600,
    sync_directory: bool = True,
) -> None:
    """Write private binary state through the atomic replacement boundary."""
    _atomic_write_stream(
        path,
        lambda file: file.write(payload),
        binary=True,
        mode=mode,
        sync_directory=sync_directory,
    )


def _atomic_write_stream(
    path: Path,
    writer: Callable[[Any], object],
    *,
    binary: bool,
    mode: int,
    sync_directory: bool,
) -> None:

    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        try:
            file: TextIO | BinaryIO = (
                os.fdopen(fd, "wb") if binary else os.fdopen(fd, "w", encoding="utf-8")
            )
        except OSError:
            os.close(fd)
            raise
        with file:
            writer(file)
            file.flush()
            os.fsync(file.fileno())
        os.chmod(temporary, mode)
        os.replace(temporary, path)
        if sync_directory and hasattr(os, "O_DIRECTORY"):
            try:
                directory_fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
            except OSError:
                directory_fd = None
            if directory_fd is not None:
                try:
                    os.fsync(directory_fd)
                except OSError:
                    pass
                finally:
                    os.close(directory_fd)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def migrate_state(kind: str, value: dict[str, Any]) -> dict[str, Any]:
    """Return a versioned copy of a supported legacy state document.

    Migration only adds unambiguous defaults and never repairs malformed values.
    Callers can therefore keep the original file when validation fails later.
    """

    migrated = _clone_state(value)
    raw_version = migrated.get("schema_version", migrated.get("version", 1))
    if isinstance(raw_version, bool) or not isinstance(raw_version, int):
        raise StateDataError("state schema version is invalid")
    if raw_version > CURRENT_SCHEMA_VERSION or raw_version < 1:
        raise StateDataError("unsupported state schema version")
    migrated.pop("version", None)
    migrated["schema_version"] = CURRENT_SCHEMA_VERSION

    if kind == "identity":
        if not isinstance(migrated.get("node_id"), str) or not isinstance(
            migrated.get("secret"), str
        ):
            raise StateDataError("identity state is invalid")
    elif kind == "trust":
        for name in ("grants", "trusted", "pending"):
            if name not in migrated:
                migrated[name] = []
            if not isinstance(migrated[name], list):
                raise StateDataError(f"trust {name} state is invalid")
        _migrate_single_routes(migrated["grants"])
        _migrate_single_routes(migrated["trusted"])
        _migrate_single_routes(migrated["pending"])
    elif kind == "cluster":
        if "used_invites" not in migrated:
            migrated["used_invites"] = []
        if not isinstance(migrated["used_invites"], list):
            raise StateDataError("cluster invite state is invalid")
    elif kind == "routes":
        routes = migrated.get("routes", [])
        _migrate_single_routes(routes)
    else:
        raise StateDataError(f"unknown state kind: {kind}")
    return migrated


def _migrate_single_routes(items: Any) -> None:
    if not isinstance(items, list):
        raise StateDataError("route state is invalid")
    for item in items:
        if not isinstance(item, dict):
            continue
        if "routes" in item:
            continue
        host, port = item.get("host"), item.get("port")
        if (
            isinstance(host, str)
            and host
            and isinstance(port, int)
            and not isinstance(port, bool)
        ):
            item["routes"] = [{"host": host, "port": port, "source": "legacy"}]


class JsonStateStore:
    def __init__(self, path: Path, *, kind: str | None = None) -> None:
        self.path = path
        self.kind = kind

    def save(self, value: dict[str, Any]) -> None:
        _save_json_document(self.path, value)

    def load(self) -> dict[str, Any]:
        try:
            value = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
            raise StateDataError("persisted state cannot be decoded") from error
        if not isinstance(value, dict):
            raise StateDataError("state root must be an object")
        document = cast(dict[str, Any], value)
        return migrate_state(self.kind, document) if self.kind else document

    def load_migrated(self, kind: str) -> dict[str, Any]:
        """Load and migrate a document without changing the on-disk file."""

        value = self.load()
        return migrate_state(kind, value)


class YamlStateStore:
    def __init__(self, path: Path, *, kind: str | None = None) -> None:
        self.path = path
        self.kind = kind

    def exists(self) -> bool:
        """Return whether YAML state or its legacy JSON source is present."""
        return self.path.exists() or self.path.with_suffix(".json").exists()

    def save(self, value: dict[str, Any]) -> None:
        _save_text_document(self.path, value)

    def load(self) -> dict[str, Any]:
        self._auto_migrate()
        try:
            text = self.path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as error:
            raise StateDataError("persisted state cannot be decoded") from error
        try:
            value = _yaml_load(text)
        except yaml.YAMLError as error:
            raise StateDataError("persisted state cannot be decoded") from error
        if not isinstance(value, dict):
            raise StateDataError("state root must be an object")
        document = cast(dict[str, Any], value)
        return migrate_state(self.kind, document) if self.kind else document

    def load_migrated(self, kind: str) -> dict[str, Any]:
        value = self.load()
        return migrate_state(kind, value)

    def _auto_migrate(self) -> None:
        if self.path.exists():
            return
        json_path = self.path.with_suffix(".json")
        if not json_path.exists():
            return
        try:
            raw: Any = json.loads(json_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
            raise StateDataError("persisted state cannot be decoded") from error
        if not isinstance(raw, dict):
            raise StateDataError("state root must be an object")
        _atomic_write(
            self.path,
            lambda file: _yaml_dump(raw, file),
        )


class MessagePackStateStore:
    """Atomically persist one bounded, string-keyed MessagePack map."""

    MAX_BYTES = 1024 * 1024

    def __init__(self, path: Path) -> None:
        self.path = path

    def save(self, value: dict[str, Any]) -> None:
        if not isinstance(value, dict) or not all(
            isinstance(key, str) for key in value
        ):
            raise StateDataError("MessagePack state must be a string-keyed map")
        try:
            payload = msgpack.packb(value, use_bin_type=True)
        except (TypeError, ValueError) as error:
            raise StateDataError("state cannot be encoded as MessagePack") from error
        if not isinstance(payload, bytes):
            raise StateDataError("MessagePack encoder returned invalid data")
        if len(payload) > self.MAX_BYTES:
            raise StateDataError("MessagePack state exceeds size limit")
        _atomic_write_bytes(self.path, payload)

    def load(self) -> dict[str, Any]:
        try:
            payload = self.path.read_bytes()
        except OSError as error:
            raise StateDataError(
                "persisted MessagePack state cannot be read"
            ) from error
        if len(payload) > self.MAX_BYTES:
            raise StateDataError("MessagePack state exceeds size limit")
        try:
            value = msgpack.unpackb(
                payload,
                raw=False,
                strict_map_key=True,
                max_str_len=self.MAX_BYTES,
                max_bin_len=self.MAX_BYTES,
                max_array_len=100_000,
                max_map_len=100_000,
                max_ext_len=0,
            )
        except (ValueError, TypeError, msgpack.UnpackException) as error:
            raise StateDataError("persisted MessagePack state is malformed") from error
        if not isinstance(value, dict) or not all(
            isinstance(key, str) for key in value
        ):
            raise StateDataError("MessagePack state root must be a string-keyed map")
        return cast(dict[str, Any], value)

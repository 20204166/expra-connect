"""Small atomic JSON persistence boundary for package-owned state."""

from __future__ import annotations

import json
import os
import tempfile
from copy import deepcopy
from pathlib import Path
from typing import Any, cast


class StateDataError(ValueError):
    """Raised when persisted state is structurally invalid or unreadable."""


CURRENT_SCHEMA_VERSION = 2


def migrate_state(kind: str, value: dict[str, Any]) -> dict[str, Any]:
    """Return a versioned copy of a supported legacy state document.

    Migration only adds unambiguous defaults and never repairs malformed values.
    Callers can therefore keep the original file when validation fails later.
    """

    migrated = deepcopy(value)
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
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, temporary = tempfile.mkstemp(
            prefix=f".{self.path.name}.", dir=self.path.parent
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as file:
                json.dump(value, file, sort_keys=True)
                file.flush()
                os.fsync(file.fileno())
            os.chmod(temporary, 0o600)
            os.replace(temporary, self.path)
            if hasattr(os, "O_DIRECTORY"):
                try:
                    directory_fd = os.open(
                        self.path.parent, os.O_RDONLY | os.O_DIRECTORY
                    )
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

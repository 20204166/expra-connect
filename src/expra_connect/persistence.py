"""Small atomic JSON persistence boundary for package-owned state."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any, cast


class StateDataError(ValueError):
    """Raised when persisted state is structurally invalid or unreadable."""


class JsonStateStore:
    def __init__(self, path: Path) -> None:
        self.path = path

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
            directory_fd = os.open(self.path.parent, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
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
        return cast(dict[str, Any], value)

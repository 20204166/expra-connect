"""Stable node identity values and small durable identity storage."""

from __future__ import annotations

import json
import os
import secrets
import tempfile
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path


@dataclass(frozen=True, slots=True)
class NodeId:
    """Stable installation identifier, never an address or credential."""

    value: str

    def __post_init__(self) -> None:
        if not self.value or self.value == "local" or len(self.value) > 128:
            raise ValueError("invalid node id")

    def __str__(self) -> str:
        return self.value


@dataclass(frozen=True, slots=True)
class NodeIdentity:
    """Persisted identity with an opaque local authentication secret."""

    node_id: NodeId
    secret: str

    def __post_init__(self) -> None:
        if len(self.secret) != 64:
            raise ValueError("identity secret must be 256-bit hex text")
        try:
            bytes.fromhex(self.secret)
        except ValueError as error:
            raise ValueError("identity secret must be hexadecimal") from error

    @classmethod
    def create(cls, node_id: NodeId | None = None) -> NodeIdentity:
        return cls(node_id or NodeId(secrets.token_hex(16)), secrets.token_hex(32))

    def to_json(self) -> str:
        return json.dumps({"node_id": str(self.node_id), "secret": self.secret})

    @classmethod
    def from_json(cls, value: str) -> NodeIdentity:
        document = json.loads(value)
        return cls(NodeId(str(document["node_id"])), str(document["secret"]))

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as file:
                file.write(self.to_json())
                file.flush()
                os.fsync(file.fileno())
            os.chmod(temporary, 0o600)
            os.replace(temporary, path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

    @classmethod
    def load(cls, path: Path) -> NodeIdentity:
        return cls.from_json(path.read_text(encoding="utf-8"))


def node_identity_fingerprint(node_id: NodeId | str) -> str:
    value = node_id.value if isinstance(node_id, NodeId) else node_id
    return sha256(value.encode("utf-8")).hexdigest()

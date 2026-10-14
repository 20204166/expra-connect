"""Stable node identity values and small durable identity storage."""

from __future__ import annotations

import base64
import json
import secrets
from dataclasses import dataclass, field
from hashlib import sha256
from pathlib import Path

import yaml
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)

from .observability import ObservabilityWatcher, observation_scope
from .persistence import (
    MessagePackStateStore,
    StateDataError,
    _atomic_write,
    _save_text_document,
)

_IDENTITY_PROFILE_SCHEMA_VERSION = 3
_IDENTITY_SECRET_SCHEMA_VERSION = 1
_IDENTITY_SECRET_FIELDS = frozenset(
    {"schema_version", "node_id", "secret", "root_private_key"}
)

_IDENTITY_JSON_FIELDS = frozenset(
    {
        "version",
        "schema_version",
        "node_id",
        "secret",
        "root_private_key",
        "device_identity_expected",
        "root_public_key",
        "secret_fingerprint",
    }
)


@dataclass(frozen=True, slots=True)
class NodeId:
    """Stable installation identifier, never an address or credential."""

    value: str

    def __post_init__(self) -> None:
        if (
            not isinstance(self.value, str)
            or not self.value
            or self.value == "local"
            or len(self.value) > 128
        ):
            raise ValueError("invalid node id")

    def __str__(self) -> str:
        return self.value


@dataclass(frozen=True, slots=True)
class NodeIdentity:
    """Persisted compatibility identity and access to the active device root."""

    node_id: NodeId
    secret: str = field(repr=False)
    root_private_key: str = field(default="", repr=False)
    device_identity_expected: bool = field(default=False, repr=False, compare=False)
    _extensions: tuple[tuple[str, object], ...] = field(
        default=(), repr=False, compare=False
    )

    def __post_init__(self) -> None:
        if not isinstance(self.node_id, NodeId):
            raise ValueError(  # noqa: TRY004 - preserve malformed-state contract.
                "identity node id is invalid"
            )
        if not isinstance(self.secret, str) or len(self.secret) != 64:
            raise ValueError("identity secret must be 256-bit hex text")
        try:
            bytes.fromhex(self.secret)
        except ValueError as error:
            raise ValueError("identity secret must be hexadecimal") from error
        if not isinstance(self.root_private_key, str):
            raise ValueError(  # noqa: TRY004 - preserve malformed-state contract.
                "invalid root signing key"
            )
        if not self.root_private_key:
            key = Ed25519PrivateKey.generate()
            object.__setattr__(
                self,
                "root_private_key",
                base64.b64encode(
                    key.private_bytes(
                        serialization.Encoding.Raw,
                        serialization.PrivateFormat.Raw,
                        serialization.NoEncryption(),
                    )
                ).decode("ascii"),
            )
        try:
            _decode_root_private_key(self.root_private_key)
        except ValueError as error:
            raise ValueError("invalid root signing key") from error
        if not isinstance(self.device_identity_expected, bool):
            raise ValueError(  # noqa: TRY004 - preserve malformed-state contract.
                "device identity expectation marker is invalid"
            )
        if not isinstance(self._extensions, tuple) or any(
            not isinstance(item, tuple)
            or len(item) != 2
            or not isinstance(item[0], str)
            or item[0] in _IDENTITY_JSON_FIELDS
            for item in self._extensions
        ):
            raise ValueError("identity extensions are invalid")

    @classmethod
    def create(cls, node_id: NodeId | None = None) -> NodeIdentity:
        return cls(node_id or NodeId(secrets.token_hex(16)), secrets.token_hex(32))

    def to_json(self) -> str:
        """Serialize the legacy full-JSON identity shape for compatibility.

        Profile persistence uses :meth:`save`, which separates metadata from
        secret-bearing MessagePack state.
        """
        document: dict[str, object] = dict(self._extensions)
        document.update(
            {
                "version": 2,
                "node_id": str(self.node_id),
                "secret": self.secret,
                "root_private_key": self.root_private_key,
            }
        )
        if self.device_identity_expected:
            document["device_identity_expected"] = True
        return json.dumps(document)

    @classmethod
    def from_json(cls, value: str) -> NodeIdentity:
        """Read a legacy full-JSON record; profile loading handles split records."""
        try:
            document = json.loads(value)
        except (TypeError, ValueError) as error:
            raise ValueError("identity document is malformed") from error
        if not isinstance(document, dict):
            raise ValueError(  # noqa: TRY004 - normalize malformed JSON state.
                "identity document is malformed"
            )
        for name in ("version", "schema_version"):
            if name in document and (
                isinstance(document[name], bool)
                or not isinstance(document[name], int)
                or document[name] not in (1, 2)
            ):
                raise ValueError("identity schema version is invalid")
        if (
            "version" in document
            and "schema_version" in document
            and document["version"] != document["schema_version"]
        ):
            raise ValueError("identity schema versions conflict")
        try:
            node_id = document["node_id"]
            secret = document["secret"]
        except KeyError as error:
            raise ValueError("identity document is malformed") from error
        if not isinstance(node_id, str) or not isinstance(secret, str):
            raise ValueError(  # noqa: TRY004 - normalize malformed JSON state.
                "identity document is malformed"
            )
        root_private_key = document.get("root_private_key", "")
        if not isinstance(root_private_key, str) or (
            "root_private_key" in document and not root_private_key
        ):
            raise ValueError("identity document is malformed")
        device_identity_expected = document.get("device_identity_expected", False)
        if not isinstance(device_identity_expected, bool):
            raise ValueError(  # noqa: TRY004 - normalize malformed JSON state.
                "identity document is malformed"
            )
        extensions = tuple(
            (name, item)
            for name, item in document.items()
            if name not in _IDENTITY_JSON_FIELDS
        )
        return cls(
            NodeId(node_id),
            secret,
            root_private_key,
            device_identity_expected,
            extensions,
        )

    def save(self, path: Path) -> None:
        MessagePackStateStore(path.with_suffix(".msgpack")).save(
            self._secret_document()
        )
        metadata = self._profile_metadata()
        _save_text_document(
            path, metadata, atomic_write=_atomic_write, sync_directory=False
        )

    @classmethod
    def load(
        cls,
        path: Path,
        *,
        observer: ObservabilityWatcher | None = None,
    ) -> NodeIdentity:
        try:
            value = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as error:
            raise ValueError("identity document is malformed") from error
        try:
            document = yaml.safe_load(value)
        except (TypeError, ValueError, yaml.YAMLError) as error:
            raise ValueError("identity document is malformed") from error
        if not isinstance(document, dict):
            raise ValueError(  # noqa: TRY004 - persisted-state failures stay ValueError.
                "identity document is malformed"
            )
        profile_version = document.get("schema_version")
        if (
            not isinstance(profile_version, bool)
            and profile_version == _IDENTITY_PROFILE_SCHEMA_VERSION
        ):
            return cls._from_profile(path, document)
        identity = cls.from_json(value)
        with observation_scope(observer, "profile:identity_migration"):
            identity.save(path)
        return identity

    def _profile_metadata(self) -> dict[str, object]:
        metadata: dict[str, object] = dict(self._extensions)
        metadata.update(
            {
                "schema_version": _IDENTITY_PROFILE_SCHEMA_VERSION,
                "node_id": self.node_id.value,
                "root_public_key": self.root_public_key,
                "secret_fingerprint": _secret_fingerprint(bytes.fromhex(self.secret)),
            }
        )
        if self.device_identity_expected:
            metadata["device_identity_expected"] = True
        return metadata

    def _secret_document(self) -> dict[str, object]:
        return {
            "schema_version": _IDENTITY_SECRET_SCHEMA_VERSION,
            "node_id": self.node_id.value,
            "secret": bytes.fromhex(self.secret),
            "root_private_key": base64.b64decode(self.root_private_key, validate=True),
        }

    @classmethod
    def _from_profile(cls, path: Path, metadata: dict[str, object]) -> NodeIdentity:
        node_id = metadata.get("node_id")
        root_public_key = metadata.get("root_public_key")
        secret_fingerprint = metadata.get("secret_fingerprint")
        expected = metadata.get("device_identity_expected", False)
        if (
            not isinstance(node_id, str)
            or not isinstance(root_public_key, str)
            or not isinstance(secret_fingerprint, str)
            or not isinstance(expected, bool)
        ):
            raise ValueError(  # noqa: TRY004 - persisted-state failures stay ValueError.
                "identity metadata is malformed"
            )
        secret_path = path.with_suffix(".msgpack")
        if not secret_path.exists():
            raise StateDataError("identity secrets are missing")
        bundle = MessagePackStateStore(secret_path).load()
        bundle_version = bundle.get("schema_version")
        bundle_node_id = bundle.get("node_id")
        secret = bundle.get("secret")
        root_private_key = bundle.get("root_private_key")
        if (
            set(bundle) != _IDENTITY_SECRET_FIELDS
            or isinstance(bundle_version, bool)
            or bundle_version != _IDENTITY_SECRET_SCHEMA_VERSION
            or bundle_node_id != node_id
            or not isinstance(secret, bytes)
            or len(secret) != 32
            or not isinstance(root_private_key, bytes)
            or len(root_private_key) != 32
        ):
            raise StateDataError("identity secret bundle is malformed or mismatched")
        private_key = Ed25519PrivateKey.from_private_bytes(root_private_key)
        public_key = private_key.public_key().public_bytes(
            serialization.Encoding.Raw, serialization.PublicFormat.Raw
        )
        if (
            base64.b64encode(public_key).decode("ascii") != root_public_key
            or _secret_fingerprint(secret) != secret_fingerprint
        ):
            raise StateDataError("identity secret bundle does not match its metadata")
        extensions = tuple(
            (name, value)
            for name, value in metadata.items()
            if name not in _IDENTITY_JSON_FIELDS
        )
        return cls(
            NodeId(node_id),
            secret.hex(),
            base64.b64encode(root_private_key).decode("ascii"),
            expected,
            extensions,
        )

    @property
    def root_public_key(self) -> str:
        public = (
            self._private_key()
            .public_key()
            .public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
        )
        return base64.b64encode(public).decode("ascii")

    def sign_transport_proof(self, generation: int, fingerprint: str) -> str:
        if (
            not isinstance(generation, int)
            or isinstance(generation, bool)
            or generation < 1
            or not isinstance(fingerprint, str)
            or not fingerprint
        ):
            raise ValueError("invalid transport proof input")
        signature = self._private_key().sign(
            _proof_payload(self.node_id, generation, fingerprint)
        )
        return base64.b64encode(signature).decode("ascii")

    def verify_transport_proof(
        self, generation: int, fingerprint: str, proof: str
    ) -> bool:
        return verify_transport_proof(
            self.node_id, self.root_public_key, generation, fingerprint, proof
        )

    def _private_key(self) -> Ed25519PrivateKey:
        return _decode_root_private_key(self.root_private_key)


def _decode_root_private_key(value: str) -> Ed25519PrivateKey:
    try:
        private_key = base64.b64decode(value, validate=True)
        if len(private_key) != 32:
            raise ValueError
        return Ed25519PrivateKey.from_private_bytes(private_key)
    except (TypeError, ValueError) as error:
        raise ValueError("invalid root signing key") from error


def _secret_fingerprint(secret: bytes) -> str:
    if not isinstance(secret, bytes) or len(secret) != 32:
        raise ValueError("identity secret must be 256-bit binary data")
    return sha256(b"expra-connect/identity-secret/v1\0" + secret).hexdigest()


def node_identity_fingerprint(node_id: NodeId | str) -> str:
    value = node_id.value if isinstance(node_id, NodeId) else node_id
    digest = sha256(f"system-analyzer-node:{value}".encode()).hexdigest()
    return ":".join(digest[index : index + 4] for index in range(0, 64, 4))


def _proof_payload(node_id: NodeId, generation: int, fingerprint: str) -> bytes:
    return (
        f"expra-connect/transport/v1\0{node_id.value}\0{generation}\0{fingerprint}"
    ).encode()


def verify_transport_proof(
    node_id: NodeId,
    root_public_key: str,
    generation: int,
    fingerprint: str,
    proof: str,
) -> bool:
    """Verify a transport proof using a previously trusted root public key."""
    if (
        not isinstance(node_id, NodeId)
        or not isinstance(root_public_key, str)
        or not isinstance(generation, int)
        or isinstance(generation, bool)
        or generation < 1
        or not isinstance(fingerprint, str)
        or not fingerprint
        or not isinstance(proof, str)
    ):
        return False
    try:
        public_bytes = base64.b64decode(root_public_key, validate=True)
        signature = base64.b64decode(proof, validate=True)
        Ed25519PublicKey.from_public_bytes(public_bytes).verify(
            signature, _proof_payload(node_id, generation, fingerprint)
        )
    except (InvalidSignature, OverflowError, TypeError, ValueError):
        return False
    return True


def __getattr__(name: str) -> object:
    """Provide the additive device identity from the identity namespace too."""

    if name in {"DeviceHardwareHint", "DeviceHardwareProvider"}:
        from . import device_hardware

        return getattr(device_hardware, name)
    if name in {"DeviceIdentity", "DeviceIdentityError", "DeviceIdentityView"}:
        from . import device_identity

        return getattr(device_identity, name)
    raise AttributeError(name)

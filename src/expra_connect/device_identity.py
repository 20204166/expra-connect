"""Canonical local representation of the durable device root."""

from __future__ import annotations

import base64
import json
import math
import re
import sys
import time
import uuid
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from hashlib import sha256
from pathlib import Path
from typing import Protocol

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)

from .identity import NodeId, NodeIdentity, _decode_root_private_key
from .persistence import StateDataError, _atomic_write


DEVICE_IDENTITY_SCHEMA_VERSION = 1
_FINGERPRINT_PREFIX = "ed25519:"
_HARDWARE_HINT_DOMAIN = b"expra-connect/device-hardware-hint/v1\0"
_MAC_PATTERN = re.compile(r"^[0-9a-f]{12}$")


class DeviceIdentityError(StateDataError):
    """Raised when durable device identity state is missing or invalid."""


class DeviceHardwareProvider(Protocol):
    """Best-effort local hardware identifiers used only as anomaly evidence."""

    def mac_addresses(self) -> Iterable[str]: ...

    def machine_id(self) -> str | None: ...


class _SystemHardwareProvider:
    def mac_addresses(self) -> tuple[str, ...]:
        values: list[str] = []
        if sys.platform.startswith("linux"):
            try:
                values.extend(
                    address.read_text(encoding="ascii")
                    for address in Path("/sys/class/net").glob("*/address")
                )
            except (OSError, UnicodeError):
                pass
        try:
            values.append(":%.2x:%.2x:%.2x:%.2x:%.2x:%.2x" % tuple(
                (uuid.getnode() >> shift) & 0xFF for shift in range(40, -1, -8)
            ))
        except (OSError, ValueError, TypeError):
            pass
        return tuple(values)

    def machine_id(self) -> str | None:
        if sys.platform.startswith("linux"):
            try:
                value = Path("/etc/machine-id").read_text(encoding="ascii").strip()
            except (OSError, UnicodeError):
                return None
            return value or None
        if sys.platform == "win32":
            try:
                import winreg

                with winreg.OpenKey(
                    winreg.HKEY_LOCAL_MACHINE,
                    r"SOFTWARE\Microsoft\Cryptography",
                ) as key:
                    value, _ = winreg.QueryValueEx(key, "MachineGuid")
                return value.strip() if isinstance(value, str) and value.strip() else None
            except (OSError, TypeError):
                return None
        return None


@dataclass(frozen=True, slots=True)
class DeviceHardwareHint:
    """Persisted digest and source presence, never the raw local values."""

    digest: str
    sources_present: tuple[str, ...]

    def __post_init__(self) -> None:
        if not re.fullmatch(r"[0-9a-f]{64}", self.digest):
            raise ValueError("hardware hint digest is invalid")
        if tuple(sorted(set(self.sources_present))) != self.sources_present:
            raise ValueError("hardware hint sources are invalid")
        if any(
            not source or not re.fullmatch(r"[a-z0-9-]+", source)
            for source in self.sources_present
        ):
            raise ValueError("hardware hint sources are invalid")


@dataclass(frozen=True, slots=True)
class DeviceIdentityView:
    """Secret-free runtime view of the durable device identity."""

    node_id: NodeId
    fingerprint: str
    created_at: float
    hardware_hint_changed: bool = False
    hardware_hint_status: str = "not_recorded"


@dataclass(frozen=True, slots=True)
class DeviceIdentity:
    """Canonical local Ed25519 root bound to an existing logical ``NodeId``."""

    node_id: NodeId
    public_key: bytes = field(repr=False)
    fingerprint: str
    created_at: float
    schema_version: int = DEVICE_IDENTITY_SCHEMA_VERSION
    hardware_hint: DeviceHardwareHint | None = None
    hardware_hint_changed: bool = field(default=False, repr=False, compare=False)
    hardware_hint_status: str = field(
        default="not_recorded", repr=False, compare=False
    )
    _private_key_bytes: bytes = field(repr=False, compare=False, default=b"")

    def __post_init__(self) -> None:
        if not isinstance(self.node_id, NodeId):
            raise ValueError("device identity node id is invalid")
        if self.schema_version != DEVICE_IDENTITY_SCHEMA_VERSION:
            raise ValueError("unsupported device identity schema")
        if not isinstance(self.public_key, bytes) or len(self.public_key) != 32:
            raise ValueError("device identity public key is invalid")
        if not isinstance(self._private_key_bytes, bytes) or len(self._private_key_bytes) != 32:
            raise ValueError("device identity private key is invalid")
        if not isinstance(self.created_at, (int, float)) or isinstance(self.created_at, bool):
            raise ValueError("device identity creation time is invalid")
        if not math.isfinite(float(self.created_at)) or self.created_at <= 0:
            raise ValueError("device identity creation time is invalid")
        if self.fingerprint != fingerprint_for_public_key(self.public_key):
            raise ValueError("device identity fingerprint is invalid")
        derived = Ed25519PrivateKey.from_private_bytes(self._private_key_bytes).public_key()
        derived_public = derived.public_bytes(
            serialization.Encoding.Raw, serialization.PublicFormat.Raw
        )
        if derived_public != self.public_key:
            raise ValueError("device identity key pair does not match")

    @classmethod
    def create(
        cls,
        node_id: NodeId,
        *,
        hardware_provider: DeviceHardwareProvider | None = None,
        clock: Callable[[], float] = time.time,
    ) -> DeviceIdentity:
        private = Ed25519PrivateKey.generate()
        private_bytes = private.private_bytes(
            serialization.Encoding.Raw,
            serialization.PrivateFormat.Raw,
            serialization.NoEncryption(),
        )
        public = private.public_key().public_bytes(
            serialization.Encoding.Raw, serialization.PublicFormat.Raw
        )
        return cls(
            node_id=node_id,
            public_key=public,
            fingerprint=fingerprint_for_public_key(public),
            created_at=float(clock()),
            hardware_hint=collect_hardware_hint(hardware_provider),
            _private_key_bytes=private_bytes,
        )

    @classmethod
    def from_node_identity(
        cls,
        node_identity: NodeIdentity,
        existing: DeviceIdentity | None = None,
        *,
        hardware_provider: DeviceHardwareProvider | None = None,
        clock: Callable[[], float] = time.time,
    ) -> DeviceIdentity:
        """Represent the active network root as the local device identity."""

        if existing is not None and existing.node_id != node_identity.node_id:
            raise DeviceIdentityError("device identity NodeId does not match local NodeId")
        try:
            private_key = _decode_root_private_key(node_identity.root_private_key)
            private_key_bytes = private_key.private_bytes(
                serialization.Encoding.Raw,
                serialization.PrivateFormat.Raw,
                serialization.NoEncryption(),
            )
        except ValueError as error:
            raise DeviceIdentityError("active node identity root is malformed") from error
        public_key = private_key.public_key().public_bytes(
            serialization.Encoding.Raw, serialization.PublicFormat.Raw
        )
        current_hint = collect_hardware_hint(hardware_provider)
        stored_hint = existing.hardware_hint if existing is not None else None
        changed, hint_status = _hardware_hint_state(stored_hint, current_hint)
        return cls(
            node_id=node_identity.node_id,
            public_key=public_key,
            fingerprint=fingerprint_for_public_key(public_key),
            created_at=(existing.created_at if existing is not None else float(clock())),
            hardware_hint=(
                stored_hint if existing is not None else current_hint
            ),
            hardware_hint_changed=changed,
            hardware_hint_status=hint_status,
            _private_key_bytes=private_key_bytes,
        )

    @classmethod
    def load(
        cls,
        path: Path,
        expected_node_id: NodeId | None = None,
        *,
        hardware_provider: DeviceHardwareProvider | None = None,
    ) -> DeviceIdentity:
        try:
            value = path.read_text(encoding="utf-8")
        except (OSError, UnicodeError) as error:
            raise DeviceIdentityError("device identity is malformed") from error
        return cls.from_json(
            value,
            expected_node_id,
            hardware_provider=hardware_provider,
        )

    @classmethod
    def from_json(
        cls,
        value: str,
        expected_node_id: NodeId | None = None,
        *,
        hardware_provider: DeviceHardwareProvider | None = None,
    ) -> DeviceIdentity:
        try:
            document = json.loads(value)
            if not isinstance(document, dict):
                raise ValueError
            identity = cls._from_document(document)
        except DeviceIdentityError:
            raise
        except (TypeError, ValueError) as error:
            raise DeviceIdentityError("device identity is malformed") from error
        if expected_node_id is not None and identity.node_id != expected_node_id:
            raise DeviceIdentityError("device identity NodeId does not match local NodeId")
        current_hint = collect_hardware_hint(hardware_provider)
        changed, hint_status = _hardware_hint_state(identity.hardware_hint, current_hint)
        return DeviceIdentity(
            node_id=identity.node_id,
            public_key=identity.public_key,
            fingerprint=identity.fingerprint,
            created_at=identity.created_at,
            schema_version=identity.schema_version,
            hardware_hint=identity.hardware_hint,
            hardware_hint_changed=changed,
            hardware_hint_status=hint_status,
            _private_key_bytes=identity._private_key_bytes,
        )

    def to_json(self) -> str:
        document: dict[str, object] = {
            "schema_version": self.schema_version,
            "node_id": self.node_id.value,
            "public_key": _encode_bytes(self.public_key),
            "private_key": _encode_bytes(self._private_key_bytes),
            "fingerprint": self.fingerprint,
            "created_at": self.created_at,
        }
        if self.hardware_hint is not None:
            document["hardware_hint_digest"] = self.hardware_hint.digest
            document["hardware_hint_sources"] = list(self.hardware_hint.sources_present)
        return json.dumps(document, sort_keys=True, separators=(",", ":"))

    def save(self, path: Path) -> None:
        _atomic_write(path, lambda file: file.write(self.to_json()))

    def public_view(self) -> DeviceIdentityView:
        return DeviceIdentityView(
            node_id=self.node_id,
            fingerprint=self.fingerprint,
            created_at=self.created_at,
            hardware_hint_changed=self.hardware_hint_changed,
            hardware_hint_status=self.hardware_hint_status,
        )

    def sign(self, data: bytes) -> bytes:
        return Ed25519PrivateKey.from_private_bytes(self._private_key_bytes).sign(data)

    def verify(self, data: bytes, signature: bytes) -> bool:
        try:
            Ed25519PublicKey.from_public_bytes(self.public_key).verify(signature, data)
        except (InvalidSignature, TypeError, ValueError):
            return False
        return True

    @classmethod
    def _from_document(cls, document: dict[object, object]) -> DeviceIdentity:
        if document.get("schema_version") != DEVICE_IDENTITY_SCHEMA_VERSION:
            raise DeviceIdentityError("unsupported device identity schema")
        try:
            raw_node_id = document["node_id"]
            if not isinstance(raw_node_id, str):
                raise ValueError
            node_id = NodeId(raw_node_id)
            public_key = _decode_bytes(document["public_key"])
            private_key = _decode_bytes(document["private_key"])
            fingerprint = document["fingerprint"]
            created_at = document["created_at"]
            digest = document.get("hardware_hint_digest")
            sources = document.get("hardware_hint_sources", [])
            if not isinstance(fingerprint, str) or not isinstance(created_at, (int, float)):
                raise ValueError
            if isinstance(created_at, bool):
                raise ValueError
            if not isinstance(sources, list) or not all(isinstance(item, str) for item in sources):
                raise ValueError
            if digest is not None and not isinstance(digest, str):
                raise ValueError
            if digest is not None and not sources:
                raise ValueError
            hardware_hint = (
                None if digest is None else DeviceHardwareHint(digest, tuple(sources))
            )
            return cls(
                node_id=node_id,
                public_key=public_key,
                fingerprint=fingerprint,
                created_at=float(created_at),
                hardware_hint=hardware_hint,
                _private_key_bytes=private_key,
            )
        except (KeyError, TypeError, ValueError, OverflowError) as error:
            raise DeviceIdentityError("device identity is malformed") from error


def fingerprint_for_public_key(public_key: bytes) -> str:
    if len(public_key) != 32:
        raise ValueError("device identity public key is invalid")
    return _FINGERPRINT_PREFIX + sha256(public_key).hexdigest()


def normalize_mac_addresses(values: Iterable[str]) -> tuple[str, ...]:
    normalized: set[str] = set()
    for value in values:
        if not isinstance(value, str):
            continue
        compact = re.sub(r"[-: .]", "", value.strip().lower())
        if not _MAC_PATTERN.fullmatch(compact):
            continue
        first_octet = int(compact[:2], 16)
        if first_octet & 1 or compact == "0" * 12:
            continue
        normalized.add(compact)
    return tuple(sorted(normalized))


def collect_hardware_hint(
    provider: DeviceHardwareProvider | None = None,
) -> DeviceHardwareHint | None:
    source = provider or _SystemHardwareProvider()
    macs = normalize_mac_addresses(_safe_macs(source))
    machine_id = _safe_machine_id(source)
    values: list[str] = []
    sources: list[str] = []
    if macs:
        values.append("macs:" + ",".join(macs))
        sources.append("mac")
    if machine_id:
        values.append("machine-id:" + machine_id)
        sources.append("machine-id")
    if not values:
        return None
    digest = sha256(_HARDWARE_HINT_DOMAIN + "\0".join(values).encode("utf-8")).hexdigest()
    return DeviceHardwareHint(digest, tuple(sources))


def _hardware_hint_state(
    stored: DeviceHardwareHint | None,
    current: DeviceHardwareHint | None,
) -> tuple[bool, str]:
    if stored is None:
        return False, "not_recorded"
    if current is None:
        return False, "unavailable"
    if stored.digest != current.digest:
        return True, "changed"
    return False, "match"


def _safe_macs(provider: DeviceHardwareProvider) -> Iterable[str]:
    try:
        return provider.mac_addresses()
    except Exception:  # noqa: BLE001 - hardware hints are best effort.
        return ()


def _safe_machine_id(provider: DeviceHardwareProvider) -> str | None:
    try:
        value = provider.machine_id()
    except Exception:  # noqa: BLE001 - hardware hints are best effort.
        return None
    return value.strip() if isinstance(value, str) and value.strip() else None


def _encode_bytes(value: bytes) -> str:
    return base64.b64encode(value).decode("ascii")


def _decode_bytes(value: object) -> bytes:
    if not isinstance(value, str):
        raise ValueError
    decoded = base64.b64decode(value, validate=True)
    if len(decoded) != 32:
        raise ValueError
    return decoded


def load_device_identity(
    profile: Path,
    node_identity: NodeIdentity,
    provider: DeviceHardwareProvider | None = None,
) -> DeviceIdentity:
    """Load or migrate the profile-owned representation of the network root."""

    path = profile / "device_identity.json"
    if path.exists():
        existing = DeviceIdentity.load(
            path, node_identity.node_id, hardware_provider=provider
        )
        identity = DeviceIdentity.from_node_identity(
            node_identity, existing, hardware_provider=provider
        )
        if identity.public_key != existing.public_key:
            identity.save(path)
        return identity
    identity = DeviceIdentity.from_node_identity(
        node_identity, hardware_provider=provider
    )
    identity.save(path)
    return identity


def device_identity_diagnostics(identity: DeviceIdentity) -> dict[str, object]:
    """Return only operational device identity fields."""

    view = identity.public_view()
    return {
        "node_id": view.node_id.value,
        "fingerprint": view.fingerprint,
        "created_at": view.created_at,
        "hardware_hint_changed": view.hardware_hint_changed,
        "hardware_hint_status": view.hardware_hint_status,
    }


class DeviceIdentityRuntimeMixin:
    """Runtime lifecycle seam for the canonical device identity state."""

    _device_identity: DeviceIdentity | None = None

    @property
    def device_identity(self) -> DeviceIdentityView | None:
        return self._device_identity.public_view() if self._device_identity else None

    def _load_device_identity(
        self,
        profile: Path,
        node_identity: NodeIdentity,
        provider: DeviceHardwareProvider | None,
    ) -> None:
        self._device_identity = load_device_identity(profile, node_identity, provider)

    def _device_identity_diagnostics(self) -> dict[str, object] | None:
        if self._device_identity is None:
            return None
        return device_identity_diagnostics(self._device_identity)

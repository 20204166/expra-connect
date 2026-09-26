"""Profile lifecycle ownership for one active runtime and its local identity."""

from __future__ import annotations

import os
from dataclasses import replace
from pathlib import Path
from typing import Any, cast

from .device_identity import (
    DeviceHardwareProvider,
    DeviceIdentity,
    load_device_identity,
)
from .identity import NodeIdentity
from .observability import ObservabilityWatcher, observation_scope
from .persistence import StateDataError


class ProfileInUseError(StateDataError):
    """Raised when another runtime already owns the profile directory."""


class ProfileLock:
    """Hold a nonblocking OS lock for the lifetime of one runtime profile."""

    def __init__(self, profile: Path) -> None:
        self.path = profile / ".expra-connect.lock"
        self._descriptor: int | None = None

    def acquire(self) -> None:
        if self._descriptor is not None:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        descriptor = os.open(self.path, os.O_CREAT | os.O_RDWR, 0o600)
        try:
            if os.name == "nt":
                msvcrt = cast(Any, __import__("msvcrt"))

                if os.fstat(descriptor).st_size == 0:
                    os.write(descriptor, b"\0")
                os.lseek(descriptor, 0, os.SEEK_SET)
                msvcrt.locking(descriptor, msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as error:
            os.close(descriptor)
            raise ProfileInUseError(
                "profile is already owned by another runtime"
            ) from error
        except BaseException:
            os.close(descriptor)
            raise
        self._descriptor = descriptor

    def release(self) -> None:
        descriptor = self._descriptor
        if descriptor is None:
            return
        self._descriptor = None
        try:
            if os.name == "nt":
                msvcrt = cast(Any, __import__("msvcrt"))

                os.lseek(descriptor, 0, os.SEEK_SET)
                msvcrt.locking(descriptor, msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(descriptor, fcntl.LOCK_UN)
        finally:
            os.close(descriptor)


def load_profile_identity(
    profile: Path,
    provider: DeviceHardwareProvider | None = None,
    observer: ObservabilityWatcher | None = None,
) -> tuple[NodeIdentity, DeviceIdentity]:
    """Load/migrate the profile identity pair or create it only for empty state."""
    with observation_scope(observer, "profile:identity_load"):
        identity = _load_or_create_node_identity(profile, observer)
        device_path = profile / "device_identity.json"
        if identity.device_identity_expected and not device_path.exists():
            raise StateDataError("expected device identity is missing")
        device_identity = load_device_identity(profile, identity, provider, observer)
        if not identity.device_identity_expected:
            identity = replace(identity, device_identity_expected=True)
            with observation_scope(observer, "profile:identity_marker_adoption"):
                identity.save(profile / "identity.json")
        return identity, device_identity


def _load_or_create_node_identity(
    profile: Path, observer: ObservabilityWatcher | None
) -> NodeIdentity:
    """Create a NodeIdentity only if the profile has no dependent state."""
    identity_path = profile / "identity.json"
    if identity_path.exists():
        return NodeIdentity.load(identity_path, observer=observer)
    if (profile / "identity.msgpack").exists():
        raise StateDataError("identity metadata is missing")

    dependent_names = {
        "device_identity.json",
        "device_identity.msgpack",
        "trust.json",
        "transport.json",
        "cluster.json",
        "idempotency.json",
        "peer-tls.crt",
        "peer-tls.key",
    }
    if profile.exists() and (
        any((profile / name).exists() for name in dependent_names)
        or next(profile.glob("transport-*.json"), None) is not None
        or next(profile.glob("peer-tls-*.crt"), None) is not None
        or next(profile.glob("peer-tls-*.key"), None) is not None
    ):
        raise StateDataError("profile state exists but the network identity is missing")

    identity = NodeIdentity.create()
    with observation_scope(observer, "profile:identity_create"):
        identity.save(identity_path)
    return identity

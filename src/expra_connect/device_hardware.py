"""Best-effort local hardware evidence used by device identity."""

from __future__ import annotations

import re
import sys
import uuid
from collections.abc import Iterable
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from typing import Protocol

_HARDWARE_HINT_DOMAIN = b"expra-connect/device-hardware-hint/v1\0"

_MAC_PATTERN = re.compile(r"^[0-9a-f]{12}$")


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
            node = uuid.getnode()
            values.append(
                ":"
                + ":".join(
                    f"{(node >> shift) & 0xFF:02x}" for shift in range(40, -1, -8)
                )
            )
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
                return (
                    value.strip() if isinstance(value, str) and value.strip() else None
                )
            except (OSError, TypeError):
                return None
        return None


@dataclass(frozen=True, slots=True)
class DeviceHardwareHint:
    """Persisted digest and source presence, never the raw local values."""

    digest: str
    sources_present: tuple[str, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.digest, str) or not re.fullmatch(
            r"[0-9a-f]{64}", self.digest
        ):
            raise ValueError("hardware hint digest is invalid")
        if not isinstance(self.sources_present, tuple) or not all(
            isinstance(source, str) for source in self.sources_present
        ):
            raise ValueError("hardware hint sources are invalid")
        if tuple(sorted(set(self.sources_present))) != self.sources_present:
            raise ValueError("hardware hint sources are invalid")
        if any(
            not source or not re.fullmatch(r"[a-z0-9-]+", source)
            for source in self.sources_present
        ):
            raise ValueError("hardware hint sources are invalid")


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
    digest = sha256(
        _HARDWARE_HINT_DOMAIN + "\0".join(values).encode("utf-8")
    ).hexdigest()
    return DeviceHardwareHint(digest, tuple(sources))


def _safe_macs(provider: DeviceHardwareProvider) -> Iterable[str]:
    try:
        return tuple(provider.mac_addresses())
    except Exception:  # noqa: BLE001 - hardware hints are best effort.
        return ()


def _safe_machine_id(provider: DeviceHardwareProvider) -> str | None:
    try:
        value = provider.machine_id()
        return value.strip() if isinstance(value, str) and value.strip() else None
    except Exception:  # noqa: BLE001 - hardware hints are best effort.
        return None

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
_DIGEST_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_HARDWARE_SOURCE_PATTERN = re.compile(r"^[a-z0-9-]+$")
_MAC_SEPARATOR_TRANSLATION = str.maketrans("", "", "-: .")


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
        if not isinstance(self.digest, str) or not _DIGEST_PATTERN.fullmatch(
            self.digest
        ):
            raise ValueError("hardware hint digest is invalid")

        sources = self.sources_present
        valid_sources = (
            isinstance(sources, tuple)
            and all(
                isinstance(source, str)
                and _HARDWARE_SOURCE_PATTERN.fullmatch(source) is not None
                for source in sources
            )
            and tuple(sorted(set(sources))) == sources
        )
        if not valid_sources:
            raise ValueError("hardware hint sources are invalid")


def normalize_mac_addresses(values: Iterable[str]) -> tuple[str, ...]:
    """Return sorted, unique, usable MAC addresses in canonical hex form."""
    normalized: set[str] = set()

    for value in values:
        if not isinstance(value, str):
            continue

        compact = value.strip().lower().translate(_MAC_SEPARATOR_TRANSLATION)
        if not _MAC_PATTERN.fullmatch(compact):
            continue

        first_octet = int(compact[:2], 16)
        if compact == "0" * 12 or first_octet & 1:
            continue

        normalized.add(compact)

    return tuple(sorted(normalized))


def collect_hardware_hint(
    provider: DeviceHardwareProvider | None = None,
) -> DeviceHardwareHint | None:
    """Collect available hardware evidence into a stable, non-sensitive digest."""
    source = provider if provider is not None else _SystemHardwareProvider()

    macs = normalize_mac_addresses(_safe_macs(source))
    machine_id = _safe_machine_id(source)

    evidence: list[tuple[str, str]] = []

    if macs:
        evidence.append(("mac", "macs:" + ",".join(macs)))

    if machine_id:
        evidence.append(("machine-id", "machine-id:" + machine_id))

    if not evidence:
        return None

    payload = "\0".join(value for _, value in evidence).encode("utf-8")
    digest = sha256(_HARDWARE_HINT_DOMAIN + payload).hexdigest()
    sources_present = tuple(source_name for source_name, _ in evidence)

    return DeviceHardwareHint(digest, sources_present)


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

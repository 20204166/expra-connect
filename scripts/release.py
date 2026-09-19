"""Repo-local release preparation for Expra Connect wheels."""

from __future__ import annotations

import argparse
import hashlib
import re
import zipfile
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

VersionBump = Literal["none", "patch", "feature", "minor"]

_VERSION_RE = re.compile(r'(__version__\s*=\s*")(?P<value>\d+\.\d+\.\d+\.\d+)(")')
_WHEEL_RE = re.compile(r"^expra_connect-(\d+\.\d+\.\d+\.\d+)-py3-none-any\.whl$")
_VERSION_FILE = Path("src") / "expra_connect" / "_version.py"
_PACKAGE_ROOT = "expra_connect"

_MINOR_SURFACES = frozenset({"expra_connect/runtime.py", "expra_connect/cli.py"})
_FEATURE_SURFACES = frozenset(
    {
        "expra_connect/connection_manager.py",
        "expra_connect/pairing_flow.py",
        "expra_connect/remote_service.py",
        "expra_connect/sharing.py",
    }
)


@dataclass(frozen=True)
class DiffSummary:
    added: tuple[str, ...]
    removed: tuple[str, ...]
    changed: tuple[str, ...]

    @property
    def has_changes(self) -> bool:
        return bool(self.added or self.removed or self.changed)


def _parse_version(raw: str) -> tuple[int, int, int, int]:
    parts = raw.split(".")
    if len(parts) != 4:
        raise ValueError(f"expected four version segments, got {raw!r}")
    return tuple(int(part) for part in parts)  # type: ignore[return-value]


def _format_version(parts: tuple[int, int, int, int]) -> str:
    return ".".join(str(part) for part in parts)


def _bump_segment(
    parts: tuple[int, int, int, int], index: int
) -> tuple[int, int, int, int]:
    values = list(parts)
    values[index] += 1
    for position in range(index, 0, -1):
        if values[position] <= 9:
            break
        values[position] = 0
        values[position - 1] += 1
    return tuple(values)  # type: ignore[return-value]


def _next_version(base: str, bump: VersionBump) -> str:
    parts = _parse_version(base)
    if bump == "none":
        return base
    if bump == "minor":
        return _format_version(_bump_segment((parts[0], parts[1], 0, 0), 1))
    if bump == "feature":
        return _format_version(_bump_segment((parts[0], parts[1], parts[2], 0), 2))
    return _format_version(_bump_segment(parts, 3))


def _compare_versions(left: str, right: str) -> int:
    l_parts = _parse_version(left)
    r_parts = _parse_version(right)
    return (l_parts > r_parts) - (l_parts < r_parts)


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _normalise_version(payload: bytes) -> bytes:
    return _VERSION_RE.sub(r"\1__VERSION__\3", payload.decode()).encode()


def _source_manifest(package_dir: Path) -> dict[str, str]:
    root = package_dir / "src" / _PACKAGE_ROOT
    manifest: dict[str, str] = {}
    for path in sorted(root.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        relative = path.relative_to(package_dir / "src").as_posix()
        payload = path.read_bytes()
        if relative == f"{_PACKAGE_ROOT}/_version.py":
            payload = _normalise_version(payload)
        manifest[relative] = _sha256(payload)
    return manifest


def _wheel_manifest(wheel: Path) -> dict[str, str]:
    with zipfile.ZipFile(wheel) as archive:
        return {
            name: _sha256(
                _normalise_version(archive.read(name))
                if name == f"{_PACKAGE_ROOT}/_version.py"
                else archive.read(name)
            )
            for name in archive.namelist()
            if name.startswith(f"{_PACKAGE_ROOT}/") and name.endswith(".py")
        }


def _newest_wheel(dist_dir: Path) -> Path | None:
    candidates: list[tuple[tuple[int, int, int, int], Path]] = []
    for path in dist_dir.glob("expra_connect-*.whl"):
        match = _WHEEL_RE.match(path.name)
        if match:
            candidates.append((_parse_version(match.group(1)), path))
    return (
        max(candidates, default=None, key=lambda item: item[0])[1]
        if candidates
        else None
    )


def _wheel_version(path: Path) -> str:
    match = _WHEEL_RE.match(path.name)
    if match is None:
        raise ValueError(f"unexpected wheel filename: {path.name}")
    return match.group(1)


def diff_manifests(
    previous: Mapping[str, str], current: Mapping[str, str]
) -> DiffSummary:
    before, after = set(previous), set(current)
    return DiffSummary(
        added=tuple(sorted(after - before)),
        removed=tuple(sorted(before - after)),
        changed=tuple(
            sorted(key for key in before & after if previous[key] != current[key])
        ),
    )


def classify_bump(diff: DiffSummary, *, override: str | None = None) -> VersionBump:
    if override and override != "auto":
        if override not in {"none", "patch", "feature", "minor"}:
            raise ValueError(f"unsupported bump override: {override}")
        return override  # type: ignore[return-value]
    if not diff.has_changes:
        return "none"
    touched = set(diff.added + diff.removed + diff.changed)
    if touched & _MINOR_SURFACES:
        return "minor"
    if touched & _FEATURE_SURFACES or diff.added or diff.removed:
        return "feature"
    return "patch"


def read_current_version(package_dir: Path) -> str:
    match = _VERSION_RE.search(
        (package_dir / _VERSION_FILE).read_text(encoding="utf-8")
    )
    if match is None:
        raise ValueError("could not find the four-segment package version")
    return match.group("value")


def write_current_version(package_dir: Path, version: str) -> None:
    path = package_dir / _VERSION_FILE
    text, count = _VERSION_RE.subn(
        rf"\g<1>{version}\g<3>", path.read_text(encoding="utf-8"), count=1
    )
    if count != 1:
        raise ValueError(f"could not update package version in {path}")
    path.write_text(text, encoding="utf-8")


def prepare_build(package_dir: Path, *, bump_override: str | None = None) -> str:
    current = read_current_version(package_dir)
    latest = _newest_wheel(package_dir / "dist")
    latest_version = _wheel_version(latest) if latest else None
    previous = _wheel_manifest(latest) if latest else {}
    diff = diff_manifests(previous, _source_manifest(package_dir))
    base = (
        latest_version
        if latest_version and _compare_versions(latest_version, current) > 0
        else current
    )
    next_version = _next_version(base, classify_bump(diff, override=bump_override))
    if next_version != current:
        write_current_version(package_dir, next_version)
    print(f"current source version: {current}")
    print(f"latest built wheel: {latest_version or 'none'}")
    print(f"selected bump: {classify_bump(diff, override=bump_override)}")
    print(f"next build version: {next_version}")
    return next_version


def newest_wheel(package_dir: Path) -> Path:
    wheel = _newest_wheel(package_dir / "dist")
    if wheel is None:
        raise FileNotFoundError("no four-segment Expra Connect wheel found")
    return wheel


def rewrite_sha256sums(package_dir: Path) -> None:
    wheel = newest_wheel(package_dir)
    (package_dir / "dist" / "SHA256SUMS").write_text(
        f"{_sha256(wheel.read_bytes())}  {wheel.name}\n", encoding="utf-8"
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Expra Connect release helper")
    sub = parser.add_subparsers(dest="command", required=True)
    prepare = sub.add_parser("prepare-build")
    prepare.add_argument("--package-dir", required=True)
    prepare.add_argument(
        "--bump", default="auto", choices=["auto", "none", "patch", "feature", "minor"]
    )
    newest = sub.add_parser("newest-wheel")
    newest.add_argument("--package-dir", required=True)
    sync = sub.add_parser("sync-artifacts")
    sync.add_argument("--package-dir", required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    package_dir = Path(args.package_dir).resolve()
    if args.command == "prepare-build":
        prepare_build(package_dir, bump_override=args.bump)
    elif args.command == "newest-wheel":
        print(newest_wheel(package_dir))
    else:
        rewrite_sha256sums(package_dir)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

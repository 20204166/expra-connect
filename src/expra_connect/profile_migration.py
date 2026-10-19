"""Explicit, schema-neutral migration between application profile directories."""

from __future__ import annotations

import os
import stat
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import BinaryIO, Literal, Protocol

from .persistence import StateDataError


class ProfileMigrationError(StateDataError):
    """A profile could not be staged, verified, or promoted safely."""


@dataclass(frozen=True, slots=True)
class ProfileMigrationResult:
    """Output written by the converter and the activated profile location."""

    destination: Path
    files_written: int
    bytes_written: int


class ProfileSourceReader(Protocol):
    """Read-only access to regular files in a legacy profile."""

    def files(self) -> tuple[str, ...]: ...

    def read_bytes(self, relative_path: str) -> bytes: ...


class StagedProfileWriter(Protocol):
    """Write-only access to files in the private staging profile."""

    def write_bytes(self, relative_path: str, payload: bytes) -> None: ...


ProfileConverter = Callable[[ProfileSourceReader, StagedProfileWriter], None]
ProfileVerifier = Callable[[Path], None]


def migrate_profile(
    source_profile: Path,
    destination_profile: Path,
    *,
    convert: ProfileConverter,
    verify: ProfileVerifier,
) -> ProfileMigrationResult:
    """Convert and verify into a new profile, retaining the source unchanged.

    The destination parent must exist and the destination must not. The host
    coordinates migration against other processes; this helper does not claim
    cross-process exclusion or a multi-file transaction.
    """

    source, destination = _validate_profile_locations(
        source_profile, destination_profile
    )
    if not callable(convert) or not callable(verify):
        raise ProfileMigrationError("migration callbacks are invalid")
    return _migrate_profile(source, destination, convert=convert, verify=verify)


def _migrate_profile(
    source: Path,
    destination: Path,
    *,
    convert: ProfileConverter,
    verify: ProfileVerifier,
) -> ProfileMigrationResult:
    try:
        with tempfile.TemporaryDirectory(
            dir=destination.parent,
            prefix=f".{destination.name}.migration-",
        ) as temporary_directory:
            stage = Path(temporary_directory) / "profile"
            stage.mkdir(mode=0o700)
            writer = _StagedProfileWriter(stage)
            _invoke_callback(
                convert,
                _ProfileSourceReader(source),
                writer,
                error_message="profile conversion failed",
            )
            if writer.files_written == 0:
                raise ProfileMigrationError("profile conversion produced no files")
            _validate_staged_tree(stage)
            _invoke_callback(verify, stage, error_message="profile verification failed")
            _validate_staged_tree(stage)
            if os.path.lexists(destination):
                raise ProfileMigrationError("profile destination is not empty")
            os.rename(stage, destination)
            return ProfileMigrationResult(
                destination=destination,
                files_written=writer.files_written,
                bytes_written=writer.bytes_written,
            )
    except ProfileMigrationError:
        raise
    except OSError:
        raise ProfileMigrationError(
            "profile migration filesystem operation failed"
        ) from None


def _validate_profile_locations(
    source_profile: Path, destination_profile: Path
) -> tuple[Path, Path]:
    try:
        source_input = Path(source_profile).expanduser()
        destination_input = Path(destination_profile).expanduser()
    except (TypeError, ValueError, OSError):
        raise ProfileMigrationError("profile locations are invalid") from None
    if source_input.is_symlink():
        raise ProfileMigrationError("source profile must not be a symbolic link")
    try:
        source = source_input.resolve(strict=True)
        parent = destination_input.parent.resolve(strict=True)
    except (OSError, RuntimeError):
        raise ProfileMigrationError(
            "profile source or destination parent is unavailable"
        ) from None
    if not source.is_dir() or not parent.is_dir():
        raise ProfileMigrationError("profile source or destination parent is invalid")
    destination = parent / destination_input.name
    if os.path.lexists(destination):
        raise ProfileMigrationError("profile destination already exists")
    if (
        source == destination
        or source in destination.parents
        or destination in source.parents
    ):
        raise ProfileMigrationError("source and destination profiles overlap")
    return source, destination


def _invoke_callback(
    callback: Callable[..., object], *args: object, error_message: str
) -> None:
    failed = False
    try:
        callback(*args)
    except Exception:  # noqa: BLE001 - host callback errors must be redacted.
        failed = True
    if failed:
        raise ProfileMigrationError(error_message)


def _relative_path(value: str) -> PurePosixPath:
    if not isinstance(value, str) or not value or "\x00" in value or "\\" in value:
        raise ProfileMigrationError("profile path is invalid")
    posix_path = PurePosixPath(value)
    windows_path = PureWindowsPath(value)
    if (
        posix_path.is_absolute()
        or windows_path.is_absolute()
        or windows_path.drive
        or not posix_path.parts
        or any(part in {"", ".", ".."} or ":" in part for part in posix_path.parts)
    ):
        raise ProfileMigrationError("profile path is invalid")
    return posix_path


class _ProfileSourceReader:
    def __init__(self, root: Path) -> None:
        if root.is_symlink() or not root.is_dir():
            raise ProfileMigrationError("source profile is invalid")
        self._root = root

    def files(self) -> tuple[str, ...]:
        found: list[str] = []

        def raise_walk_error(_error: OSError) -> None:
            raise ProfileMigrationError("source profile cannot be inspected")

        for current, directories, filenames in os.walk(
            self._root, topdown=True, followlinks=False, onerror=raise_walk_error
        ):
            directories.sort()
            filenames.sort()
            current_path = Path(current)
            for name in directories:
                _require_directory(current_path / name)
            for name in filenames:
                file_path = current_path / name
                _require_regular_file(file_path)
                found.append(file_path.relative_to(self._root).as_posix())
                _relative_path(found[-1])
        return tuple(sorted(found))

    def read_bytes(self, relative_path: str) -> bytes:
        relative = _relative_path(relative_path)
        path = self._root.joinpath(*relative.parts)
        _require_safe_components(self._root, relative, final_kind="file")
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0)
        try:
            with _open_binary_stream(path, flags, mode="rb") as stream:
                return stream.read()
        except OSError:
            raise ProfileMigrationError("source profile file cannot be read") from None


class _StagedProfileWriter:
    def __init__(self, root: Path) -> None:
        if root.is_symlink() or not root.is_dir():
            raise ProfileMigrationError("staging profile is invalid")
        self._root = root
        self._written_paths: set[str] = set()
        self.files_written = 0
        self.bytes_written = 0

    def write_bytes(self, relative_path: str, payload: bytes) -> None:
        relative = _relative_path(relative_path)
        if not isinstance(payload, bytes):
            raise ProfileMigrationError("profile payload must be bytes")
        normalized = relative.as_posix()
        if normalized in self._written_paths:
            raise ProfileMigrationError("profile output path is duplicated")
        parent = self._root
        for component in relative.parts[:-1]:
            parent = parent / component
            try:
                parent.mkdir(mode=0o700)
            except FileExistsError:
                pass
            _require_directory(parent)
        path = parent / relative.parts[-1]
        if os.path.lexists(path):
            raise ProfileMigrationError("profile output path already exists")
        flags = (
            os.O_WRONLY
            | os.O_CREAT
            | os.O_EXCL
            | getattr(os, "O_NOFOLLOW", 0)
            | getattr(os, "O_BINARY", 0)
        )
        try:
            with _open_binary_stream(path, flags, mode="wb", file_mode=0o600) as stream:
                if os.name == "posix":
                    os.fchmod(stream.fileno(), 0o600)
                if stream.write(payload) != len(payload):
                    raise OSError("short profile write")
                stream.flush()
                os.fsync(stream.fileno())
        except OSError:
            raise ProfileMigrationError("profile output could not be written") from None
        self._written_paths.add(normalized)
        self.files_written += 1
        self.bytes_written += len(payload)


def _open_binary_stream(
    path: Path,
    flags: int,
    *,
    mode: Literal["rb", "wb"],
    file_mode: int | None = None,
) -> BinaryIO:
    descriptor = (
        os.open(path, flags) if file_mode is None else os.open(path, flags, file_mode)
    )
    try:
        return os.fdopen(descriptor, mode)
    except BaseException:
        try:
            os.close(descriptor)
        except OSError:
            pass
        raise


def _require_safe_components(
    root: Path, relative: PurePosixPath, *, final_kind: str
) -> None:
    if root.is_symlink() or not root.is_dir():
        raise ProfileMigrationError("profile directory is invalid")
    current = root
    for index, component in enumerate(relative.parts):
        current = current / component
        is_final = index == len(relative.parts) - 1
        if is_final and final_kind == "file":
            _require_regular_file(current)
        else:
            _require_directory(current)


def _require_directory(path: Path) -> None:
    try:
        mode = path.lstat().st_mode
    except OSError:
        raise ProfileMigrationError("profile directory cannot be inspected") from None
    if not stat.S_ISDIR(mode):
        raise ProfileMigrationError("profile path contains a non-directory")


def _require_regular_file(path: Path) -> None:
    try:
        mode = path.lstat().st_mode
    except OSError:
        raise ProfileMigrationError("profile file cannot be inspected") from None
    if not stat.S_ISREG(mode):
        raise ProfileMigrationError("profile path contains a non-regular file")


def _validate_staged_tree(root: Path) -> int:
    if root.is_symlink() or not root.is_dir():
        raise ProfileMigrationError("staging profile is invalid")
    file_count = 0

    def raise_walk_error(_error: OSError) -> None:
        raise ProfileMigrationError("staging profile cannot be inspected")

    for current, directories, filenames in os.walk(
        root, topdown=True, followlinks=False, onerror=raise_walk_error
    ):
        current_path = Path(current)
        for name in directories:
            _require_directory(current_path / name)
        for name in filenames:
            _require_regular_file(current_path / name)
            file_count += 1
    if file_count == 0:
        raise ProfileMigrationError("staging profile contains no files")
    return file_count

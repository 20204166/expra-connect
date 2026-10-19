from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

import expra_connect as package
from expra_connect.profile_migration import (
    ProfileMigrationError,
    ProfileSourceReader,
    StagedProfileWriter,
    _ProfileSourceReader,
    _StagedProfileWriter,
    migrate_profile,
)


class ProfileMigrationTests(unittest.TestCase):
    def test_public_migration_api_is_exported_from_package_root(self) -> None:
        self.assertIs(package.ProfileMigrationError, ProfileMigrationError)
        self.assertIs(package.ProfileSourceReader, ProfileSourceReader)
        self.assertIs(package.StagedProfileWriter, StagedProfileWriter)
        self.assertEqual(
            package.ProfileMigrationResult.__module__, ProfileMigrationError.__module__
        )
        self.assertIs(package.migrate_profile, migrate_profile)

    def test_migration_stages_verifies_promotes_and_preserves_source(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "legacy"
            destination = root / "connect"
            source.mkdir()
            (source / "legacy.json").write_bytes(b"synthetic")
            verified: list[Path] = []

            def convert(
                reader: ProfileSourceReader, writer: StagedProfileWriter
            ) -> None:
                writer.write_bytes("identity.yaml", reader.read_bytes("legacy.json"))

            def verify(staged: Path) -> None:
                self.assertFalse(destination.exists())
                self.assertEqual((staged / "identity.yaml").read_bytes(), b"synthetic")
                (staged / "runtime-created-state").write_bytes(b"checked")
                verified.append(staged)

            result = migrate_profile(
                source, destination, convert=convert, verify=verify
            )

            self.assertEqual(result.destination, destination)
            self.assertEqual(result.files_written, 1)
            self.assertEqual(result.bytes_written, 9)
            self.assertEqual(len(verified), 1)
            self.assertTrue(destination.is_dir())
            self.assertEqual((destination / "identity.yaml").read_bytes(), b"synthetic")
            self.assertEqual(
                (destination / "runtime-created-state").read_bytes(), b"checked"
            )
            self.assertEqual((source / "legacy.json").read_bytes(), b"synthetic")

    def test_existing_destination_is_never_overwritten(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "legacy"
            destination = root / "connect"
            source.mkdir()
            destination.mkdir()
            sentinel = destination / "keep"
            sentinel.write_bytes(b"existing")

            with self.assertRaises(ProfileMigrationError):
                migrate_profile(
                    source, destination, convert=lambda *_: None, verify=lambda _: None
                )

            self.assertEqual(sentinel.read_bytes(), b"existing")

    def test_destination_created_by_verifier_is_preserved_and_stage_removed(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "legacy"
            destination = root / "connect"
            source.mkdir()
            (source / "legacy.json").write_bytes(b"source")

            def convert(
                reader: ProfileSourceReader, writer: StagedProfileWriter
            ) -> None:
                writer.write_bytes("identity.yaml", reader.read_bytes("legacy.json"))

            def verify(_staged: Path) -> None:
                destination.mkdir()
                (destination / "owner").write_bytes(b"other")

            with self.assertRaises(ProfileMigrationError):
                migrate_profile(source, destination, convert=convert, verify=verify)

            self.assertEqual((destination / "owner").read_bytes(), b"other")
            self.assertEqual((source / "legacy.json").read_bytes(), b"source")
            self.assertEqual(list(root.glob(".connect.migration-*")), [])

    def test_callback_errors_are_redacted_and_owned_stage_is_removed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "legacy"
            source.mkdir()
            (source / "legacy.json").write_bytes(b"secret fixture")

            def convert(
                reader: ProfileSourceReader, writer: StagedProfileWriter
            ) -> None:
                writer.write_bytes("identity.yaml", reader.read_bytes("legacy.json"))
                raise RuntimeError("credential-value-must-not-escape")

            with self.assertRaises(ProfileMigrationError) as caught:
                migrate_profile(
                    source,
                    root / "connect",
                    convert=convert,
                    verify=lambda _: None,
                )

            self.assertNotIn("credential-value-must-not-escape", str(caught.exception))
            self.assertIsNone(caught.exception.__context__)
            self.assertEqual(list(root.glob(".connect.migration-*")), [])
            self.assertEqual((source / "legacy.json").read_bytes(), b"secret fixture")

    def test_verifier_failure_keeps_source_and_removes_only_stage(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "legacy"
            source.mkdir()
            (source / "legacy.json").write_bytes(b"legacy")

            def convert(
                reader: ProfileSourceReader, writer: StagedProfileWriter
            ) -> None:
                writer.write_bytes("identity.yaml", reader.read_bytes("legacy.json"))

            def fail_verification(_staged: Path) -> None:
                raise RuntimeError("private")

            with self.assertRaises(ProfileMigrationError):
                migrate_profile(
                    source,
                    root / "connect",
                    convert=convert,
                    verify=fail_verification,
                )

            self.assertFalse((root / "connect").exists())
            self.assertEqual((source / "legacy.json").read_bytes(), b"legacy")
            self.assertEqual(list(root.glob(".connect.migration-*")), [])

    def test_rejects_missing_source_parent_and_empty_conversion(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "legacy"
            source.mkdir()
            destination = root / "connect"

            with self.assertRaises(ProfileMigrationError):
                migrate_profile(
                    root / "missing",
                    destination,
                    convert=lambda *_: None,
                    verify=lambda _: None,
                )
            with self.assertRaises(ProfileMigrationError):
                migrate_profile(
                    source,
                    root / "missing-parent" / "connect",
                    convert=lambda *_: None,
                    verify=lambda _: None,
                )
            with self.assertRaises(ProfileMigrationError):
                migrate_profile(
                    source,
                    destination,
                    convert=lambda *_: None,
                    verify=lambda _: None,
                )

            self.assertFalse(destination.exists())
            self.assertEqual(list(root.glob(".connect.migration-*")), [])

    def test_reader_rejects_traversal_and_source_symlinks(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "legacy"
            source.mkdir()
            outside = root / "outside"
            outside.write_bytes(b"outside")
            (source / "link").symlink_to(outside)
            reader = _ProfileSourceReader(source)

            for relative_path in ("", "../outside", "/outside", "C:\\outside"):
                with (
                    self.subTest(relative_path=relative_path),
                    self.assertRaises(ProfileMigrationError),
                ):
                    reader.read_bytes(relative_path)
            with self.assertRaises(ProfileMigrationError):
                reader.files()
            self.assertEqual(outside.read_bytes(), b"outside")

    def test_writer_rejects_invalid_paths_duplicates_and_symlink_parents(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            stage = root / "stage"
            stage.mkdir()
            outside = root / "outside"
            outside.mkdir()
            (stage / "link").symlink_to(outside, target_is_directory=True)
            writer = _StagedProfileWriter(stage)

            for relative_path in ("", "../outside/file", "/outside", "C:\\outside"):
                with (
                    self.subTest(relative_path=relative_path),
                    self.assertRaises(ProfileMigrationError),
                ):
                    writer.write_bytes(relative_path, b"x")
            with self.assertRaises(ProfileMigrationError):
                writer.write_bytes("link/file", b"outside")
            writer.write_bytes("nested/file", b"first")
            with self.assertRaises(ProfileMigrationError):
                writer.write_bytes("nested/file", b"second")

            self.assertEqual(list(outside.iterdir()), [])
            self.assertEqual((stage / "nested/file").read_bytes(), b"first")

    def test_writer_output_uses_private_mode_on_posix(self) -> None:
        if os.name != "posix":
            self.skipTest("POSIX file mode semantics are unavailable")
        with tempfile.TemporaryDirectory() as directory:
            stage = Path(directory) / "stage"
            stage.mkdir()
            writer = _StagedProfileWriter(stage)

            writer.write_bytes("nested/state", b"synthetic")

            self.assertEqual((stage / "nested/state").stat().st_mode & 0o777, 0o600)


if __name__ == "__main__":
    unittest.main()

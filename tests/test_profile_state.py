import json
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

import yaml

from expra_connect.identity import NodeIdentity
from expra_connect.profile_state import ProfileInUseError, ProfileLock


class ProfileLockTests(unittest.TestCase):
    def test_lock_excludes_another_process_and_releases_after_owner_exit(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            profile = Path(directory)
            ready = profile / "ready"
            release = profile / "release"
            child = """
import sys
import time
from pathlib import Path
from expra_connect.profile_state import ProfileLock
profile = Path(sys.argv[1])
lock = ProfileLock(profile)
lock.acquire()
(profile / 'ready').touch()
deadline = time.monotonic() + 10
while not (profile / 'release').exists():
    if time.monotonic() >= deadline:
        raise SystemExit(2)
    time.sleep(0.01)
lock.release()
"""
            process = subprocess.Popen([sys.executable, "-c", child, str(profile)])
            try:
                deadline = time.monotonic() + 5
                while not ready.exists() and time.monotonic() < deadline:
                    time.sleep(0.01)
                self.assertTrue(ready.exists(), "child did not acquire the profile")

                contender = ProfileLock(profile)
                with self.assertRaises(ProfileInUseError):
                    contender.acquire()

                release.touch()
                self.assertEqual(process.wait(timeout=5), 0)
                contender.acquire()
                contender.release()
            finally:
                release.touch(exist_ok=True)
                if process.poll() is None:
                    process.terminate()
                    process.wait(timeout=5)


class ProfileIdentityYamlMigrationTests(unittest.TestCase):
    def test_load_profile_identity_creates_yaml_not_json(self) -> None:
        from expra_connect.profile_state import load_profile_identity

        with tempfile.TemporaryDirectory() as directory:
            profile = Path(directory)
            load_profile_identity(profile)
            self.assertTrue(
                (profile / "identity.yaml").exists(),
                "identity.yaml not created",
            )
            self.assertFalse(
                (profile / "identity.json").exists(),
                "identity.json created when yaml expected",
            )

    def test_load_profile_identity_auto_migrates_json_to_yaml(self) -> None:
        from expra_connect.profile_state import load_profile_identity

        with tempfile.TemporaryDirectory() as directory:
            profile = Path(directory)
            identity = NodeIdentity.create()
            legacy = profile / "identity.json"
            identity.save(legacy)
            load_profile_identity(profile)
            self.assertTrue(
                (profile / "identity.yaml").exists(),
                "identity.yaml not created after migration",
            )
            self.assertTrue(legacy.exists(), "legacy JSON was deleted")
            migrated_legacy = json.loads(legacy.read_text(encoding="utf-8"))
            self.assertNotIn("secret", migrated_legacy)
            self.assertNotIn("root_private_key", migrated_legacy)

    def test_identity_yaml_content_is_valid_yaml_with_no_secrets(self) -> None:
        from expra_connect.profile_state import load_profile_identity

        with tempfile.TemporaryDirectory() as directory:
            profile = Path(directory)
            identity, _ = load_profile_identity(profile)
            content = (profile / "identity.yaml").read_text(encoding="utf-8")
            doc = yaml.safe_load(content)
            self.assertIsInstance(doc, dict)
            self.assertEqual(doc.get("schema_version"), 3)
            self.assertNotIn("secret", doc)
            self.assertNotIn("root_private_key", doc)
            self.assertEqual(doc.get("node_id"), identity.node_id.value)


if __name__ == "__main__":
    unittest.main()

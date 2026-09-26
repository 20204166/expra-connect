import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

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


if __name__ == "__main__":
    unittest.main()

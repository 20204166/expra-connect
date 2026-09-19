import subprocess
import sys
import unittest


class PackagingSurfaceTests(unittest.TestCase):
    def test_cli_exposes_help_and_version_without_network_activity(self) -> None:
        help_result = subprocess.run(
            [sys.executable, "-m", "expra_connect.cli", "--help"],
            check=False,
            capture_output=True,
            text=True,
        )
        version_result = subprocess.run(
            [sys.executable, "-m", "expra_connect.cli", "--version"],
            check=False,
            capture_output=True,
            text=True,
        )

        self.assertEqual(help_result.returncode, 0)
        self.assertIn("expra-peer", help_result.stdout)
        self.assertEqual(version_result.returncode, 0)
        self.assertRegex(version_result.stdout.strip(), r"^\d+\.\d+\.\d+\.\d+$")

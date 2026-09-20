import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


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

    def test_public_models_are_importable_and_diagnostics_are_json_values(self) -> None:
        code = (
            "from expra_connect import "
            "CapabilityShare, DiscoveredNodeCandidate, NodeCapability, "
            "NodePermission, PairingManager, PeerGrant, PendingPairing, "
            "TrustedPeer"
        )
        import_result = subprocess.run(
            [sys.executable, "-c", code],
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(import_result.returncode, 0, import_result.stderr)
        with tempfile.TemporaryDirectory() as directory:
            result = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "expra_connect.cli",
                    "--profile",
                    str(Path(directory) / "profile"),
                    "--no-discovery",
                    "diagnostics",
                ],
                check=False,
                capture_output=True,
                text=True,
            )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('"state": "started"', result.stdout)

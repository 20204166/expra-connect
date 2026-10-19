from __future__ import annotations

import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EXAMPLE = ROOT / "examples" / "host_integration.py"


class HostIntegrationExampleTests(unittest.TestCase):
    def test_help_documents_explicit_network_opt_in(self) -> None:
        result = subprocess.run(
            [sys.executable, str(EXAMPLE), "--help"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("--serve", result.stdout)

    def test_default_invocation_does_not_start_runtime(self) -> None:
        result = subprocess.run(
            [sys.executable, str(EXAMPLE)],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("No runtime started", result.stdout)
        self.assertNotIn("Runtime started", result.stdout)


if __name__ == "__main__":
    unittest.main()

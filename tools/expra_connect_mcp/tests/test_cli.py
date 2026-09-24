from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from expra_connect_dev_mcp.cli import main


class CliTests(unittest.TestCase):
    def test_config_init_writes_toml_without_overwriting(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.toml"

            self.assertEqual(main(["config", "init", "--path", str(path)]), 0)
            self.assertIn("schema_version = 1", path.read_text(encoding="utf-8"))
            self.assertEqual(main(["config", "init", "--path", str(path)]), 2)


if __name__ == "__main__":
    unittest.main()

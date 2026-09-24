from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from expra_connect_dev_mcp.config import load_config, resolve_python


class ConfigTests(unittest.TestCase):
    def test_loads_workspace_and_read_only_reference(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            reference = root / "reference"
            reference.mkdir()
            config_path = root / ".expra-connect-mcp.toml"
            config_path.write_text(
                """
schema_version = 1
[workspace]
connect_root = "."
python = "auto"
[references.exp_ui]
path = "reference"
read_only = true
""",
                encoding="utf-8",
            )

            config = load_config(config_path)

            self.assertEqual(config.workspace.connect_root, root)
            self.assertEqual(config.references["exp_ui"].path, reference)
            self.assertTrue(config.references["exp_ui"].read_only)

    def test_auto_prefers_workspace_venv(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            interpreter = root / ".venv" / "bin" / "python"
            interpreter.parent.mkdir(parents=True)
            interpreter.write_text("", encoding="utf-8")
            interpreter.chmod(0o755)

            resolved = resolve_python(root, "auto", current=Path("/fallback/python"))

            self.assertEqual(resolved.path, interpreter)
            self.assertFalse(resolved.used_fallback)


if __name__ == "__main__":
    unittest.main()

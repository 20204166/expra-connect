from __future__ import annotations

import unittest
from pathlib import Path


class RuntimeDecouplingTests(unittest.TestCase):
    def test_runtime_source_and_project_metadata_do_not_depend_on_mcp(self) -> None:
        repository = Path(__file__).resolve().parents[3]
        runtime_source = repository / "src" / "expra_connect"
        source_text = "\n".join(
            path.read_text(encoding="utf-8") for path in runtime_source.rglob("*.py")
        )
        project_text = (repository / "pyproject.toml").read_text(encoding="utf-8")

        self.assertNotIn("import mcp", source_text)
        self.assertNotIn("expra_connect_dev_mcp", source_text)
        self.assertNotIn("mcp", project_text.lower())


if __name__ == "__main__":
    unittest.main()

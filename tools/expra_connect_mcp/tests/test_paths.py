from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from expra_connect_dev_mcp.paths import PathAccessError, resolve_source_path


class ResolveSourcePathTests(unittest.TestCase):
    def test_resolves_regular_relative_path(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / "src" / "module.py"
            target.parent.mkdir()
            target.write_text("value = 1", encoding="utf-8")

            self.assertEqual(resolve_source_path(root, "src/module.py"), target)

    def test_rejects_traversal_absolute_and_symlink_escape(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            outside = root.parent / f"{root.name}-outside.txt"
            outside.write_text("secret", encoding="utf-8")
            link = root / "link.txt"
            link.symlink_to(outside)

            for value in ("../secret", "/etc/passwd", "link.txt", "bad\x00name"):
                with self.subTest(value=value), self.assertRaises(PathAccessError):
                    resolve_source_path(root, value)


if __name__ == "__main__":
    unittest.main()

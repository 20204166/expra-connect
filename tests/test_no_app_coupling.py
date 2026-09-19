import unittest
from pathlib import Path


class NoAppCouplingTests(unittest.TestCase):
    def test_source_does_not_reference_ui_or_scanner_modules(self) -> None:
        source = "\n".join(
            path.read_text(encoding="utf-8")
            for path in Path("src/expra_connect").rglob("*.py")
        )
        for forbidden in ("tkinter", "maintenance", "scanner", "system_analyzer"):
            self.assertNotIn(forbidden, source.lower())

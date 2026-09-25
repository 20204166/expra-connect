import unittest
from pathlib import Path


class NoAppCouplingTests(unittest.TestCase):
    def test_source_does_not_reference_ui_or_scanner_modules(self) -> None:
        source_root = Path(__file__).parents[1] / "src" / "expra_connect"
        source = "\n".join(
            path.read_text(encoding="utf-8") for path in source_root.rglob("*.py")
        )
        for forbidden in ("tkinter", "maintenance", "scanner", "system_analyzer"):
            self.assertNotIn(forbidden, source.lower())

"""Keep importable application modules within the repository size budget."""

from __future__ import annotations

import unittest
import warnings
from pathlib import Path

SOFT_LINE_LIMIT = 900
HARD_LINE_LIMIT = 1000
SOURCE_ROOT = Path(__file__).parents[1] / "src" / "expra_connect"


class CodeSizeTests(unittest.TestCase):
    def test_python_modules_stay_below_hard_limit(self) -> None:
        oversized = []
        soft_overages = []
        for path in sorted(SOURCE_ROOT.glob("*.py")):
            lines = len(path.read_text(encoding="utf-8").splitlines())
            if lines > HARD_LINE_LIMIT:
                oversized.append(f"{path.name}: {lines} lines")
            elif lines > SOFT_LINE_LIMIT:
                soft_overages.append(f"{path.name}: {lines} lines")
        if soft_overages:
            warnings.warn(
                "module exceeds the 900-line consolidation threshold: "
                + ", ".join(soft_overages),
                stacklevel=1,
            )
        self.assertFalse(
            oversized,
            "modules over 1000 lines require consolidation before further growth: "
            + ", ".join(oversized),
        )


if __name__ == "__main__":
    unittest.main()

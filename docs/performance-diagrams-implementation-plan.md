# Performance Diagrams Implementation Plan

> **Superseded:** The current implementation is documented in
> [`performance-diagrams-design.md`](performance-diagrams-design.md). The
> standalone charts in this original plan were replaced by
> `docs/images/performance-comparison.svg`; do not recreate them.

> **For agentic workers:** Follow the tasks in order with review checkpoints; keep the existing benchmark tables unchanged.

**Goal:** Generate four distinct, accurate SVG diagrams from the performance-baseline Markdown tables and make refresh/check operations repeatable.

**Architecture:** Keep `docs/native-wheel-performance-baseline.md` as the only benchmark-data source. A standard-library updater parses and validates the relevant Markdown tables, deterministically renders four standalone SVGs, and supports write and stale-check CLI modes. Unit tests exercise the parser, data gaps, and SVG generation.

**Tech Stack:** Python 3.12, standard library (`argparse`, `dataclasses`, `html`, `pathlib`, `re`, `xml.etree.ElementTree`), `unittest`, existing Ruff/Pyright/Mypy tooling.

---

## File map

- Create `scripts/update_performance_diagrams.py`: parse canonical Markdown tables, validate expected rows/versions, render SVG assets, and implement `--check`.
- Create `tests/test_performance_diagrams.py`: deterministic parser, missing-data, and generated-SVG tests.
- Create `docs/images/performance-overview.svg`: full-workload metric heatmap for 30k/100k/200k.
- Create `docs/images/performance-state-scaling.svg`: role decode and cluster round-trip panels.
- Create `docs/images/performance-operation-latency.svg`: identity, lookup, session, and request panels.
- Create `docs/images/performance-persistence.svg`: full-workload and focused JSON/MessagePack persistence panels with explicit gaps.
- Modify `docs/native-wheel-performance-baseline.md`: replace the one existing Mermaid chart with four image embeds and updater instructions; retain all data and pairwise tables.

## Task 1: Add parser and input-contract tests

**Files:** Create `tests/test_performance_diagrams.py`; the updater is not
implemented yet.

- [ ] Write a small-table test for `parse_markdown_table(table_text)` and a
  full-baseline test for `parse_baseline(markdown)`. Parsed row values align to
  that table's version headers; p50 values are floats and `N/A` is `None`.
- [ ] Assert all seven full-workload versions and required rows are parsed at
  sizes 30,000, 100,000, and 200,000. Assert both 200k MessagePack rows contain
  seven `None` values.
- [ ] Assert focused persistence tables retain their six recorded versions;
  rendering must later show 2.2.0.3 as unmeasured, not as a fabricated value.
- [ ] Assert a missing required metric row raises `ValueError` containing that
  row label.
- [ ] Run ` .venv/bin/python -m unittest discover -s tests -p test_performance_diagrams.py -v`.
  Expected before implementation: import failure for
  `scripts.update_performance_diagrams`.

Test fixture shape:

```python
TABLE = """## Full representative workload, 200,000 entries
| Workload (p50 µs; 200,000 entries) | 1.8.0.0 | 2.2.0.3 |
|---|---:|---:|
| Identity fingerprint | 4.793 | 3.532 |
| MessagePack store save | N/A | N/A |
"""


class BaselineParserTests(unittest.TestCase):
    def test_preserves_values_and_explicit_gaps(self) -> None:
        parsed = parse_markdown_table(TABLE)
        self.assertEqual(parsed.versions, ("1.8.0.0", "2.2.0.3"))
        self.assertEqual(parsed.rows["Identity fingerprint"], (4.793, 3.532))
        self.assertEqual(parsed.rows["MessagePack store save"], (None, None))
```

### Task 2: Implement Markdown-table parsing and validation

**Files:** Modify `scripts/update_performance_diagrams.py`;
`tests/test_performance_diagrams.py`.

- [ ] Load `senior-python-engineering` before the first Python write; use the
  general and testing scopes. Keep benchmark tables as the sole data source.
- [ ] Implement frozen `WorkloadTable(versions, rows)` and `BaselineData`
  records plus `parse_baseline(markdown: str) -> BaselineData`.
- [ ] Parse pipe-delimited rows only under the three full-workload headings and
  two focused-persistence headings. Strip thousands separators before
  `float()` conversion; map `N/A` to `None`.
- [ ] Validate exact seven full-workload versions, required workload labels,
  and the expected six versions in focused persistence tables. Raise a useful
  `ValueError` naming the missing or malformed section/row.
- [ ] Run the parser tests and confirm the tests from Task 1 pass; add negative
  tests for malformed numbers and a missing section before considering parsing
  complete.

### Task 3: Render the four deterministic SVG diagrams

**Files:** Modify `scripts/update_performance_diagrams.py`;
`tests/test_performance_diagrams.py`.

- [ ] Add one SVG helper that creates escaped text, explicit dimensions,
  labelled axes/legend, and deterministic element order. Use a color-blind-safe
  sequential palette and pair color with text/value labels.
- [ ] Render `performance-overview.svg` as three size panels, with each metric
  row normalized to its fastest measured p50 (`value / row_min`); show exact
  p50 in a tooltip and render missing values as `N/A`.
- [ ] Render `performance-state-scaling.svg` as two panels (role decode and
  cluster round-trip) with size on x and p50 microseconds on a log-scaled y-axis.
- [ ] Render `performance-operation-latency.svg` as faceted panels for the
  identity, role/grant lookup, session, and request metrics; separate unlike
  magnitude groups and show each available size/version value.
- [ ] Render `performance-persistence.svg` with separate full-workload and
  focused-persistence panels. Keep JSON/MessagePack and encode/decode/load/save
  distinct; use `N/A` for unsupported or unmeasured cells.
- [ ] Test that all four SVGs are well-formed XML, include every expected
  workload/version label, are deterministic across two renders, and visibly
  distinguish `N/A` from a measured zero. Run the focused unittest command.

### Task 4: Add refresh/check CLI and embed the diagrams

**Files:** Modify `scripts/update_performance_diagrams.py`,
`tests/test_performance_diagrams.py`, and
`docs/native-wheel-performance-baseline.md`.

- [ ] Implement `main(argv: Sequence[str] | None = None) -> int`. Default mode
  writes four files under `docs/images/`; `--check` compares generated content
  byte-for-byte and returns 1 with each stale/missing path, or 0 when current.
- [ ] Resolve repository paths from `Path(__file__).resolve()` so the command
  works from any current directory. Do not write outside the repository.
- [ ] Test write mode in a temporary directory, then test check mode for both
  current and deliberately stale/missing SVG files.
- [ ] Replace the existing Mermaid chart only with four `<img>` Markdown
  embeds, descriptive alt text, and a short update command/source-of-truth
  note. Put each chart next to its related table section, add the all-workload
  overview at the comparison section, and keep the 200k session-seeding caveat
  visible beside the operation chart. Preserve all benchmark tables and
  pairwise matrices byte-for-byte.
- [ ] Generate the four committed SVG assets by running
  `.venv/bin/python scripts/update_performance_diagrams.py`; immediately run
  `.venv/bin/python scripts/update_performance_diagrams.py --check` and expect
  exit 0.

### Task 5: Full verification and commit

**Files:** All files listed in the file map.

- [ ] Run focused tests:
  `.venv/bin/python -m unittest discover -s tests -p test_performance_diagrams.py -v`.
- [ ] Run repository validation required by `AGENTS.md`:
  `.venv/bin/python -m unittest discover -s tests -t .`, `ruff check .`,
  `ruff format --check .`, `pyright`, and
  `.venv/bin/mypy --ignore-missing-imports src tests`.
- [ ] Run updater `--check`, `git diff --check`, and inspect the full diff to
  confirm all existing tables and benchmark values remain unchanged. Do not
  rerun or alter any benchmark measurements.
- [ ] Stage only the updater, its tests, four generated SVGs, and the baseline
  Markdown. Commit as `docs: add generated performance diagrams`.

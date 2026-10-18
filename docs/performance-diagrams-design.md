# Performance diagrams design

## Goal

Add four distinct, readable diagrams to
[`native-wheel-performance-baseline.md`](../native-wheel-performance-baseline.md)
that make version and workload differences easier to compare while keeping
every existing benchmark and comparison table intact. Diagram updates should
be repeatable after the tables change, without hand-copying chart values.

## Source of truth and update workflow

- Existing Markdown tables remain the canonical benchmark data. Do not move or
  rewrite their values into a second data file.
- A small standard-library Python updater parses those tables, validates the
  expected version columns and workload rows, and generates four SVG files in
  `docs/images/`.
- The baseline Markdown embeds the generated SVGs with descriptive alt text.
- Running `.venv/bin/python scripts/update_performance_diagrams.py` refreshes
  the SVGs; `--check` verifies that committed SVGs match the current tables and
  exits nonzero when they are stale or table parsing is incomplete.
- Automated tests cover table parsing, chart input coverage, missing/N/A
  values, and SVG output structure. The updater must fail clearly on malformed
  or unexpectedly changed source tables rather than silently omit data.

## Four diagrams

1. **All-workload overview:** A per-size heatmap showing every available
   full-workload metric against all seven versions. Normalize each metric
   against the fastest value within that size/metric so unlike units are not
   combined as raw magnitudes. Preserve unavailable cells as explicit gaps.
2. **State scaling:** Role-state decode and cluster-state round-trip across
   30k, 100k, and 200k for all versions. Keep the two metrics in separate
   panels with labelled p50 units and a scale that makes size trends readable.
3. **Operation latency:** Identity fingerprint/create, role and grant lookup,
   session current-check/resume, and request sign/verify/replay. Display the
   full-workload values by size/version, with unlike magnitude groups separated
   so small operation timings remain legible.
4. **Persistence:** Show full-workload JSON/MessagePack store operations and
   focused persistence measurements as separately labelled panels. Preserve
   every recorded codec result; mark 200k MessagePack as unsupported and absent
   2.2.0.3 focused measurements as unavailable. Do not imply these distinct
   harnesses or fixture shapes are directly equivalent.

The all-workload overview is intentionally an index across the other three
views; the state, operation, and persistence diagrams each have a distinct
metric scope. Retain all current numeric tables and pairwise comparison
matrices.

## Visual and data rules

- Use a consistent, color-blind-conscious sequential scale for relative
  performance; lower p50 is faster. Include a visible legend and do not rely on
  color alone—show values or labels in each cell.
- Include version labels, workload size, p50 units, and clear `N/A`/unavailable
  markings where data is unsupported or not measured.
- Include the 2.2.0.3 full-workload data where present, but do not fabricate
  focused persistence data for that wheel.
- Keep the 200k session caveat visible: the registry was seeded directly to
  measure check/resume, not session creation.
- SVG output must be self-contained, deterministic, reasonably sized, and
  render in GitHub Markdown. Provide meaningful alt text for each diagram.

## Scope and validation

- Do not rerun benchmarks or alter benchmark measurements as part of diagram
  generation.
- Do not delete, reorder, or replace existing data tables.
- Validate with the updater's tests, its `--check` mode, and `git diff --check`.
- Update the baseline doc with a short instruction linking the updater command
  and explaining that its tables are the source of truth.

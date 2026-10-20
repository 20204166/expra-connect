# Performance diagrams design

## Goal

Generate a readable, comprehensive historical comparison in
[`native-wheel-performance-baseline.md`](../native-wheel-performance-baseline.md)
that retains 1.8.0.0 as its long-range anchor, presents a clearly defined
composite index, and retains suite-level detail. Keep the newest complete
native/universal pair as the recent reference for incremental performance work;
do not treat the historical chart as a controlled before/after test.

## Source of truth and update workflow

- The shared-harness JSON runs and versioned native/universal raw JSON in
  `docs/performance-build-comparison-results/` are the source data.
- The updater validates run provenance and generates one SVG in `docs/images/`.
- The historical composite is the geometric mean of common measured case
  ratios relative to 1.8.0.0, with equal weights for cluster state,
  persistence, and connect.
  Cases not measured for every column are omitted from the composite and remain
  visible as explicit gaps or results in the suite panels.
- The updater removes only the explicitly named superseded performance SVGs;
  all raw benchmark JSON remains intact.
- The baseline Markdown embeds the generated SVGs with descriptive alt text.
- Run `.venv/bin/python -m scripts.update_performance_diagrams` to refresh the
  diagram; add `--check` to verify it and confirm that no superseded SVG remains.
- Automated tests cover benchmark coverage, missing/N/A values, the composite
  calculation, SVG output, and removal of superseded chart files.

## Unified diagram

The SVG contains a top composite row plus separate cluster-state, connect, and
persistence panels. Every panel uses the historical release columns and the
newest available native/universal columns. The score uses common measured cases
only; suite results remain visible independently and gaps are never fabricated.

## Visual and data rules

- Use a consistent, color-blind-conscious sequential scale for relative
  performance; lower p50 is faster. Include a visible legend and do not rely on
  color alone—show values or labels in each cell.
- Include version labels, workload size, p50 units, and clear `N/A`/unavailable
  markings where data is unsupported or not measured.
- Include 2.2.0.3 full-workload data where present, but do not fabricate
  focused persistence data for that release.
- Preserve applicable workload caveats in the accompanying baseline text.
- SVG output must be self-contained, deterministic, reasonably sized, and
  render in GitHub Markdown. Provide meaningful alt text for each diagram.

## Scope and validation

- Do not rerun benchmarks or alter benchmark measurements as part of diagram
  generation.
- Do not delete, reorder, or replace existing data tables.
- Validate with the updater tests, its `--check` mode, SVG XML parsing, and
  `git diff --check`.
- Update the baseline doc with refresh instructions and the composite-score
  definition and limitations.

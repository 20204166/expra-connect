# Recent-reference performance visualization design

## Goal

Make generated performance tables and diagrams distinguish the long-range
historical comparison from the recent reference used for incremental
performance work. Keep 1.8.0.0 as the historical anchor and establish 2.2.1.0
native/universal as the recent reference pair.

## Design

- Preserve the existing across-release chart and its scores relative to 1.8.0.0.
- Add a generated recent-reference summary and SVG panel based on the saved
  2.2.1.0 native and universal benchmark results.
- Mark each 2.2.1.0 reference flavor as index `1.000`. Compare the nearest
  prior native build with the native reference and the nearest prior universal
  build with the universal reference. Compute each comparison only from the
  positive-p50 cases matched between the relevant pair; report case counts and
  configuration differences instead of silently mixing incompatible runs.
- Display normalized scores as percentages of the reference: `100%` is the
  2.2.1.0 reference, below `100%` is faster, and above `100%` is slower. Keep
  enough precision to show meaningful differences, without scientific notation
  or long raw decimals.
- Label the reference version/flavor and index basis directly in the generated
  Markdown and SVG. Keep historical and recent scores separate, not on one
  unlabeled scale.
- Leave raw benchmark JSON and measurements unchanged. The latest-build
  comparison should continue rolling forward so that 2.2.1.0 becomes the
  candidate's reference when a newer complete release pair is measured.

## Data and ownership

`scripts/update_performance_diagrams.py` remains the canonical owner of score
calculation, comparison validation, Markdown summary generation, and SVG
rendering. It should use the existing versioned native/universal benchmark
history and saved 2.2.1.0 measurements; no duplicate scoring implementation,
benchmark rerun, or manual SVG edits are in scope.

## Validation

- Add tests for matched-case scoring, 100% reference values, flavor-specific
  comparisons, missing/unsupported cases, and explicit labels in generated
  Markdown/SVG.
- Regenerate the report and diagrams from tracked JSON, then run the focused
  performance-diagram test suite, Ruff, the updater's `--check`, SVG parsing,
  and `git diff --check`.

## Scope exclusions

- Do not rebase or remove the 1.8.0.0 historical comparison.
- Do not change benchmark measurements, coverage inventories, or release-wheel
  artifacts.
- Do not claim that the saved 2.2.1.0 data are a controlled before/after test
  for arbitrary future code changes; controlled change-level claims still
  require matching environment, workload, and configuration on both revisions.

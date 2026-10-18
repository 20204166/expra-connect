# Release Performance Automation and Weighted Comparison

**Status:** Proposed for user review
**Date:** 2026-10-19

## Goal

When a release wheel is built with `scripts/build_release.sh`, benchmark only
the wheel artifacts built in that invocation, preserve their results as
versioned inputs, and refresh the across-release performance SVG. Keep all
previous benchmark results; never rebuild or rerun historical wheels as part of
this workflow.

Add one clearly defined overall measured-performance winner, while retaining
the existing equal-suite and equal-case summaries and keeping workload support
separate from latency scoring.

## Release flow

1. `build_release.sh` prepares and verifies native and, when `--universal` is
   requested, universal wheel artifacts as it does today.
2. Before release artifacts are synchronized into `dist/`, benchmark each
   newly built wheel artifact only. The benchmark runner imports the package
   from that artifact rather than from the editable source checkout.
3. Run the shared benchmark families used by the comparison: cluster state at
   1k, 30k, and 100k; persistence at 30k, 100k, and 200k; and the connect
   workload suite. Preserve unsupported and unavailable outcomes with their
   reasons; do not replace them with zero or synthetic latency values.
4. Write each result to a new version-and-flavor-specific JSON filename in
   `docs/performance-build-comparison-results/`. Refuse to overwrite existing
   versioned result files. Native-only builds add only a native result set;
   builds requested with `--universal` add native and universal result sets.
5. Load the tracked historical shared-harness JSON plus all versioned
   historical and newly generated build results, then regenerate the existing
   unified SVG and associated documentation. Preserve prior results and
   existing panels.
6. If benchmarking, result validation, or chart generation fails, stop before
   syncing release wheels. Provide an explicit `--skip-performance-benchmarks`
   escape hatch for intentional wheel-only builds; skipping must not alter or
   delete previous performance data or diagrams.

The build updates files in the checkout but does not commit or push them.
Publishing remains a human-reviewed Git operation.

## Historical data and reproducibility

Track the already-saved `tools/shared-benchmark-results/` JSON artifacts
(approximately 888 KB in the current checkout) so a clean checkout can rebuild
the chart. This is a data-preservation change only: do not rerun any historical
wheel. Keep the existing 2.2.0.4 build comparison JSON unchanged. New run files
must use unique versioned names, allowing the chart generator to discover
release/flavor columns without replacing legacy files.

The build benchmark runs on the current build host. Store interpreter, platform,
module provenance, configuration, fixture metadata, p50 values, and unavailable
reasons as the existing benchmark schema requires. Comparisons remain
descriptive across host/harness/configuration differences, not causal claims.

## Overall index and weighting

The new primary overall index is a weighted geometric mean of p50 ratios against
1.8.0.0. It uses these five categories, each weighted 20%:

1. Cluster state at 1k.
2. Cluster state at 30k.
3. Cluster state at 100k.
4. Connect operations.
5. JSON persistence at 200k.

Within each category, include only positive-p50 cases measured for every plotted
release/build and weight those cases equally. Thus every currently scoreable
case participates, while each case’s contribution differs by category size.
For the current 37 common cases, this yields the following per-case weights:

- 1k cluster: 1/45 each (9 cases share 20%).
- 30k cluster: 1/30 each (6 cases share 20%).
- 100k cluster: 1/30 each (6 cases share 20%).
- Connect: 1/50 each (10 cases share 20%).
- 200k JSON persistence: 1/30 each (6 cases share 20%).

The overall index is unitless and lower is faster. In the current saved data,
the lowest score under this weighting is 2.2.0.3 at approximately 0.889; this is
the winner for the declared measured workload set and weighting, not a claim of
universal real-world superiority. Both 2.2.0.4 builds remain separate plotted
columns. The current equal-case full-workload index and equal-suite composite
remain visible as secondary summaries so readers can see the effect of the
weighting choice.

## Coverage and unavailable cases

Keep coverage outside the latency formula; do not invent a penalty for a
missing or unsupported measurement. Expand the support matrix to cover all
distinct workload families represented by the chart, including focused 30k and
100k persistence, connect, cluster, and 200k persistence. Show measured/total
case counts per release/build and retain unavailable reasons in SVG tooltips.
Show missing 2.2.0.3 focused-persistence measurements, YAML limits, and
unsupported 200k MessagePack StateStore cases explicitly. Any repeated
visualization of the same underlying benchmark case (for example, the separate
cluster round-trip scaling panel) must not be counted twice in aggregate
coverage.

The overall latency index excludes cases not measured with a positive p50 for
every plotted release/build. Coverage explains those exclusions separately.
In particular, focused 30k/100k persistence is visible in coverage but is not
part of the current five-category score because 2.2.0.3 lacks those runs.

## Diagram and documentation changes

- Discover historical build-result sets and append new version/flavor columns
  in deterministic version order.
- Add a primary weighted overall index summary and point plot with the
  lowest-index build clearly marked.
- Retain the current equal-suite composite, equal-case index, size-specific
  plots, support/coverage view, N/A reasons, and interpretation caveats.
- Regenerate the unified `docs/images/performance-comparison.svg` and update
  `docs/native-wheel-performance-baseline.md` with the workflow, weighting,
  current winner, and coverage limitations.

## Validation

- Test weighting using synthetic runs with known case/group ratios, including
  missing and unsupported outcomes, and verify category weights and winner
  annotation.
- Test discovery and ordering of legacy 2.2.0.4 plus versioned new native-only
  and native/universal result sets; verify old files are never overwritten.
- Test release orchestration with controlled benchmark subprocesses so tests
  do not execute real wheel benchmarks.
- Test that benchmark or chart failures prevent artifact synchronization and
  that the explicit skip option preserves existing results and diagrams.
- Test support counts and reasons for all distinct displayed workload families
  without double-counting the cluster-scaling view.
- Run chart tests, repository unittest suite, Ruff, formatting, Pyright, Mypy,
  SVG freshness, and diff checks; render and inspect the updated SVG.

## Out of scope

- Rerunning or rebuilding prior wheels.
- Assigning arbitrary latency scores to unsupported, unmeasured, or
  non-positive-p50 cases.
- Claiming a universal performance winner independent of this benchmark scope,
  host, configuration, and declared weights.
- Automatically committing or pushing benchmark results, documentation, or
  diagrams.

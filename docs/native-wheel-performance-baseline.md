# Native and shared-harness performance baselines

- **Historical shared-harness measurements:** 18 October 2026
- **2.2.0.3 native/universal rerun:** 19 October 2026
- **Runtime:** CPython 3.12.3, Linux x86_64, glibc 2.39
- **Compared results:** 1.8.0.0, 1.9.0.1, 2.0.0.0, 2.0.0.2, 2.2.0.0,
  2.2.0.1, 2.2.0.3 native/universal, and 2.2.0.4 native/universal.

## What the diagram shows

- One consolidated comparison of historical releases and native/universal builds.
- Composite, cluster-state, persistence, and connect heatmaps.
- Weighted and full-workload indices, workload coverage, and four size-specific
  matched-workload plots.
- Ratios are relative to 1.8.0.0. Values below `1×` are faster; index values
  are unitless and lower is better.
- Results describe this benchmark mix. They are not predictions for every
  deployment workload.

## How scores and coverage work

- **Composite index:** geometric mean of matched p50 ratios. Cluster state,
  persistence, and connect receive equal suite weight; cases are equally
  weighted within each suite.
- **Weighted overall index (primary winner score):** geometric mean of matched
  p50 ratios, averaged within categories before applying these weights:
  - 1k cluster: 5%
  - 30k cluster: 15%
  - 100k cluster: 25%
  - Connect: 20%
  - 200k JSON persistence: 35%
- **Full-workload index:** equal-case geometric mean across the 37 positive-p50
  cases shared by every displayed result. It includes cluster state at 1k,
  30k, and 100k; matched connect cases; and 200k JSON persistence. It differs
  from the composite, which gives equal weight to the three suites.
- Only positive p50 results present in every compared column contribute to
  latency scores. Coverage is reported separately; missing cases receive no
  invented latency penalty.
- **Workload support / coverage:** the newest native/universal runs define the
  case inventory for each suite and size. Older results are measured against
  that inventory: new cases appear as unmeasured, and historical-only cases do
  not increase the denominator.
- Coverage includes cluster state at 1k, 30k, and 100k; connect; and persistence
  at 30k, 100k, and 200k. Cluster round-trip scaling reuses cases and is not
  counted twice.
- YAML size limits, unavailable connect operations, and the two unsupported
  200k MessagePack StateStore cases are explained in chart tooltips.

## Visible history window

- Keep at most 10 columns: the 1.8.0.0 baseline plus the newest results.
- Keep the baseline first/left and at the top of vertical plots. Order the
  remaining results chronologically, with the newest last/right/bottom.
- Use this same window for heatmaps, plots, summary winners, and index scores.
- When a new result exceeds the window, move the oldest non-baseline raw JSON
  into a sibling `history/` folder. Keep archived data, but exclude it from
  normal refreshes and current scoring.

<!-- performance-summary:start -->
- **Current weighted winner:** 2.2.0.3 universal — 0.858 index; 37 matched positive-p50 cases.
  - Applies only to this workload set and weighting; it is not a universal claim.
- **Distinct-workload coverage:** 73 unique cases; reported separately from latency scoring.
  - 1.8.0.0: 63/73
  - 1.9.0.1: 65/73
  - 2.0.0.0: 65/73
  - 2.0.0.2: 65/73
  - 2.2.0.0: 65/73
  - 2.2.0.1: 65/73
  - 2.2.0.3 native: 68/73
  - 2.2.0.3 universal: 68/73
  - 2.2.0.4 native: 68/73
  - 2.2.0.4 universal: 68/73
  - Missing or unsupported cases receive no invented latency penalty.
<!-- performance-summary:end -->

## Diagram and size-specific plots

![Unified across-release, native/universal, and composite performance comparison.](images/performance-comparison.svg)

- The composite uses cluster-state 1k and 30k/100k cases, connect cases, and
  200k JSON-persistence cases.
- 200k MessagePack StateStore is unsupported by payload/array limits.
- Focused 30k/100k persistence is excluded from the composite because it is not
  measured for every displayed release/build. Those results remain in the
  persistence heatmaps when available.
- Older releases have one column. 2.2.0.3 and 2.2.0.4 have separate native and
  universal columns.
- Size plots normalize each workload's p50 against its matching 1.8.0.0 result,
  then calculate a geometric mean over cases measured in every displayed result.
  Current panels cover:
  - 9 cluster-state cases at 1k
  - 6 cluster-state cases at 30k
  - 6 cluster-state cases at 100k
  - 6 JSON-persistence cases at 200k
- Each size panel marks its **lowest observed index**. Panels are descriptive,
  not a universal ranking. Workload mixes differ by size, so points are not
  connected and do not represent one scaling curve.

## Refresh and source data

- The release workflow benchmarks the wheel artifacts for that build and
  refreshes the chart before artifact synchronization.
- A failed benchmark or chart refresh prevents artifact synchronization.
- Use `--skip-performance-benchmarks` for an intentional wheel-only build.
- Existing historical results are reused; prior wheels are not rebuilt or rerun.
- The 2.2.0.3 native and universal wheels were rerun as a matched full matrix on
  CPython 3.12.3. These results replace the former single 2.2.0.3 shared-harness
  records.
- Versioned build JSON is stored in
  `docs/performance-build-comparison-results/`. Shared-harness JSON is tracked
  in `tools/shared-benchmark-results/` for reproducible refreshes.
- The shared harness records p50, fixture shape, module provenance, package
  version, and support reasons.

## Interpretation and limitations

- Older cluster-state runs used fewer warmups and repetitions than 2.2.0.4;
  cross-version ratios are descriptive, not controlled causal comparisons.
- Native suites ran before universal suites. Cache, thermal, and time-order
  effects may influence pairwise results.
- Allocation tracking was disabled; `peak_bytes` is null.
- Historical and shared-harness runs use different harnesses and fixture
  scales. Do not treat them as one ratio series or as controlled before/after
  measurements.
- The 2.2.0.1 wheel first includes `expra_connect.persistence` as a compiled
  Cython extension. Cythonizing the wrapper does not compile JSON, MessagePack,
  or filesystem operations.
- The 2.2.0.0-to-2.2.0.1 comparison is not a source-identical Cython A/B test:
  source changed, and 2.2.0.1 adds persistence to the compiled module set.
  Timing differences cannot be attributed solely to Cython.
- Save timings include filesystem synchronization and are device-sensitive;
  read timings are affected by cache state.
- These workloads do not cover live discovery, socket transport, TLS,
  concurrency, or every public API.

## Wheel artifacts

- Package source files are unchanged from 2.2.0.3 apart from version metadata.
- Native and universal wheels differ in compiled-module versus Python-source
  representation.
- Native wheel SHA-256:
  `ed1faf57de9c8b5f77aaf1d67bd077af47e0f0ef3b5835f2d41d59bb103c71f1`
- Universal wheel SHA-256:
  `2207230eb5731d20cd50b375734715c0e99376630bacf873ceebc9f0d27d75fe`

## Refresh commands

The committed SVG is embedded above. Refresh and verify it with:

```sh
.venv/bin/python -m scripts.update_performance_diagrams
.venv/bin/python -m scripts.update_performance_diagrams --check
```

# Native and shared-harness performance baselines

**Historical measurements:** 18 October 2026<br>
**Runtime:** CPython 3.12.3, Linux x86_64, glibc 2.39<br>
**Packages:** 1.8.0.0, 1.9.0.1, 2.0.0.0, 2.0.0.2, 2.2.0.0, 2.2.0.1, and 2.2.0.3.

One consolidated diagram compares the earlier releases and separate native and
universal build columns. It contains the composite index, cluster, persistence,
and connect panels, four size-specific matched-workload index panels, a weighted
overall index, and distinct-workload coverage.
Cells compare matching p50 measurements with 1.8.0.0; below 1× is faster. The
composite uses the geometric mean of matched case ratios, with cluster state,
persistence, and connect weighted equally. Within each suite, cases receive
equal weight. The index is unitless and lower is better; it is a summary of this
specific benchmark mix, not a prediction for every deployment workload.

The **weighted overall index** is the primary winner score. It computes a
geometric mean of matched p50 ratios against 1.8.0.0, first averaging cases
within each category and then applying these category weights: 1k cluster 5%,
30k cluster 15%, 100k cluster 25%, connect 20%, and 200k JSON persistence 35%.
Larger state workloads therefore have more influence; the chart labels every
weight. The currently measured winner and per-build coverage are regenerated
from the saved run data below. This is the winner for the declared workload set
and weighting, not a universal claim.
Only cases with positive p50 results for every plotted column enter latency
scores. Coverage is reported separately and does not add penalties.

The Composite section also reports a **full-workload index**: one geometric
mean across all 37 positive-p50 cases shared by every displayed release/build,
with each case weighted equally. It includes cluster-state cases at 1k, 30k,
and 100k, matched connect cases, and 200k JSON-persistence cases. This differs
from the adjacent composite, which first combines results within suites and
then weights the three suites equally. The full-workload index is still specific
to this benchmark mix and does not imply a universal winner. Focused 30k/100k
persistence cases are excluded because 2.2.0.3 was not measured there; YAML and
unsupported 200k MessagePack cases have no common positive-p50 result to score.
The full-workload index also has its own point-plot section below, using the
same release/build series and index values as the summary row above.

The **Workload support / coverage** section reports measured cases over the
union of every distinct workload case in scope, separately from latency
scoring. It shows cluster state at 1k, 30k, and 100k; connect; and persistence
at 30k, 100k, and 200k. Coverage uses the union of distinct cases in the saved
runs; the separate cluster round-trip scaling visualization reuses cases and is
not counted again. Current measured/total counts are regenerated below. The two
200k MessagePack StateStore cases are unsupported by payload/array limits; YAML
size limits and unavailable connect operations are explained in chart tooltips.
Missing cases receive no invented latency penalty.

<!-- performance-summary:start -->
**Current weighted winner:** 2.2.0.3 (0.918 index; 37 matched positive-p50 cases). This result is specific to the declared workload set and weighting, not a universal performance claim.

**Distinct-workload coverage (separate from latency scoring):** 73 unique cases; 1.8.0.0: 63/73; 1.9.0.1: 65/73; 2.0.0.0: 65/73; 2.0.0.2: 65/73; 2.2.0.0: 65/73; 2.2.0.1: 65/73; 2.2.0.3: 39/73; 2.2.0.4 native: 68/73; 2.2.0.4 universal: 68/73. Missing or unsupported cases receive no invented latency penalty.
<!-- performance-summary:end -->

Only cases with measured results for every compared column contribute to the
composite. It uses cluster 1k and 30k/100k cases, connect cases, and 200k JSON
persistence cases; 200k MessagePack StateStore is unsupported by payload/array
limits. Focused 30k/100k persistence is omitted from the composite because
2.2.0.3 was not measured there, but those measurements remain visible in the
persistence panels where available. Earlier versions have one version column;
2.2.0.4 native and universal are separate columns.

![Unified across-release, native/universal, and composite performance comparison.](images/performance-comparison.svg)

The appended size panels normalize each workload's p50 against its matching
1.8.0.0 result, then take the geometric mean across cases measured for every
displayed release/build. They show 9 cluster-state cases at 1k, 6 cluster-state
cases at each of 30k and 100k, and 6 JSON-persistence cases at 200k. Persistence
results at 30k/100k are not included because 2.2.0.3 was not measured there;
unsupported 200k MessagePack cases are also excluded. Each panel marks
its **lowest observed index** (lower is faster). These are per-panel descriptive
summaries, not a universal release ranking. Workload mix changes across sizes,
so the points are intentionally unconnected and do not describe one scaling
curve.

The release build workflow benchmarks only wheel artifacts in that build,
preserving versioned result files and refreshing the chart before release
artifact synchronization. Use `--skip-performance-benchmarks` for an
intentional wheel-only build. A failed benchmark or chart refresh prevents
artifact synchronization. Existing historical result JSON is reused; prior
wheels are not rebuilt or rerun.

The shared harness records p50, fixture shape, module provenance, package
version, and support reasons. Older cluster-state runs used fewer warmups and
repetitions than the 2.2.0.4 runs, so those cross-version ratios are descriptive
rather than controlled causal comparisons. The native suite triplet ran before
the universal triplet; cache, thermal, and time-order effects may influence
their pairwise differences. Allocation tracking was disabled, so `peak_bytes`
is null. No historical wheel was rerun. The raw 2.2.0.4 JSON is in
`docs/performance-build-comparison-results/`; the saved shared-harness JSON is
tracked in `tools/shared-benchmark-results/` for reproducible chart updates.

Package source files are unchanged from 2.2.0.3 apart from version metadata.
The native and universal wheels differ in compiled-module versus Python-source
representation. Native wheel SHA-256 is
`ed1faf57de9c8b5f77aaf1d67bd077af47e0f0ef3b5835f2d41d59bb103c71f1`; the
universal wheel SHA-256 is
`2207230eb5731d20cd50b375734715c0e99376630bacf873ceebc9f0d27d75fe`.

## Interpretation and limitations

- The 2.2.0.1 wheel is the first in this comparison that includes
  `expra_connect.persistence` as a compiled Cython extension. Cythonizing the
  wrapper does not compile JSON, MessagePack, or filesystem operations.
- The 2.2.0.0-to-2.2.0.1 comparison is not a source-identical Cython A/B test:
  source changed, and the new release adds persistence to the compiled module
  set. Do not attribute timing differences solely to Cython.
- Save timings include filesystem synchronization and remain sensitive to
  filesystem/device state. Read timings are affected by cache state.
- Historical and shared-harness runs use different harnesses and fixture
  scales. They must not be used as one ratio series or treated as controlled
  before/after measurements.
- These representative workloads do not measure live network discovery,
  socket transport, TLS, concurrent load, or every public API.

## Refresh

The committed SVG is embedded above. To rebuild it, the tracked shared-run and
build-comparison JSON artifacts must be present in the working tree:

```sh
.venv/bin/python -m scripts.update_performance_diagrams
.venv/bin/python -m scripts.update_performance_diagrams --check
```

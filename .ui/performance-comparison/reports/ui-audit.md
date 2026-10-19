# TEMPORARY AUDIT WORKING FILE — DO NOT COMMIT

## Follow-up spacing review — 2026-10-19

```text
task_type: visual polish
stack: Python-generated SVG embedded in Markdown
surface_size: one multi-panel documentation graphic
evidence_availability: renderable
mode: visual polish
risk: low
```

- **Finding UI-004 (layout debt, medium):** The comparison SVG declared a
  4,836px viewBox height while its final point-panel labels reached y=4,857,
  clipping content at the bottom. Reproduced in Firefox headless at 1,385px
  width. The full SVG is displayed as a single Markdown image; the embedded
  image scales to the Markdown content width without horizontal overflow at
  375, 768, and 1,385 CSS px, though the data-dense text naturally becomes
  smaller at narrow widths.
- **Design-language fit:** Keep the established white canvas, Arial typography,
  blue/orange/gray data palette, compact grid, and static SVG. Add only vertical
  space needed by the final panel and a small bottom margin; do not redesign the
  layout or change content ordering.
- **Implementation:** `scripts/update_performance_diagrams.py` now calculates
  the trailing SVG height from the rendered sections and shared panel spacing
  values. `tests/test_performance_diagrams.py` checks that labels and plot
  markers remain inside the viewBox.
- **Rendered evidence:** before screenshot at
  `evidence/screenshots/before/full-chart-1385.png`; after screenshots at
  `evidence/screenshots/after/markdown-width-{375,768,1385}.png`.
- **Opposition:** the read-only reviewer accepted the local sizing approach but
  requested shared layout constants and rendered viewport evidence. Shared
  constants are used, and all three viewport renders were captured and inspected.
- **Scope:** Only chart layout and its test changed; benchmark data and scoring
  are untouched.

# Audit — Expra Connect size-by-size performance comparison

## 0. Executive verdict

- **Mode:** area + rendered-SVG UI audit; implementation remains deferred pending user direction.
- **Verdict:** Existing shared-harness data can support a descriptive per-size normalized point comparison for 1k, 30k, 100k, and 200k. It cannot support one same-workload scaling curve across all four sizes: the measured suite/case mix changes, especially at 200k.
- **Blocking findings:** A connected line graph plus “best at each size” wording risks implying comparable scaling and a universal winner. Opposition recommends separate per-size point panels, no connecting lines, and “lowest observed index” wording.
- **Architecture stop/continue:** Keep `scripts/update_performance_diagrams.py` as the one generator/owner. No new chart-generation subsystem is justified.
- **Confidence:** High for source/data coverage and rendered existing chart; medium for proposed graph readability until the revised point-panel version is rendered.

## 1. Scope and exclusions

- **Repository:** Expra Connect
- **Root:** `/home/btn17/Downloads/expra-connect`
- **Target:** existing unified diagram, benchmark run groups, score semantics, and proposed size trend.
- **User goal:** preserve the current diagram and append a 1k/30k/100k/200k all-release/build comparison with a per-version graph and a clear per-size result.
- **Included:** `scripts/update_performance_diagrams.py`, `tests/test_performance_diagrams.py`, `docs/native-wheel-performance-baseline.md`, generated SVG, and shared-harness input JSON coverage.
- **Excluded:** changing benchmark results, rerunning historical wheels, changing benchmark/runtime behavior, changing the existing chart panels, committing/pushing, and unrelated dirty source changes.
- **External research:** not used; the question is answered by repository data and current chart implementation.
- **Repository MCP:** not needed for the local audit.

## 2. Checkout baseline

- **Branch / HEAD:** `main` / `8029941` (`Update performance follow-up findings`), tracking `origin/main`.
- **Release tag:** `2.2.0.4` points to the wheel-artifact source commit; not relevant to chart ownership.
- **Working tree:** dirty. User-owned modifications observed in `scripts/benchmark_support.py`, `src/expra_connect/remote/session.py`, `src/expra_connect/remote/wire_protocol.py`, `tests/test_benchmark_support.py`, `tests/test_protocol.py`, `tests/test_session.py`, and `tools/connect_performance_benchmark.py`. They were not edited or staged.
- **Audit artifacts:** `.ui/performance-comparison/` and `.superpowers/brainstorm/648443-1792362400/` were created for this audit/mockup and are untracked. Preserve them until the user decides whether to keep/remove them.
- **Actual source/data path:** chart generator reads local shared-harness JSON in `tools/shared-benchmark-results/` plus paired 2.2.0.4 runs in `docs/performance-build-comparison-results/`.
- **Build/test environment:** existing local `.venv`; no benchmark was rerun.

## 3. Repository / target topology

```text
shared-benchmark-results/*.json ─┐
2.2.0.4 paired JSON ────────────┴─> load_shared_harness_runs
                                      -> _shared_harness_panels
                                      -> _composite_performance_row
                                      -> render_diagrams
                                      -> performance-comparison.svg
                                      -> native-wheel-performance-baseline.md
                                      -> sync_diagrams / --check
tests/test_performance_diagrams.py ──> loading, coverage, SVG and sync contracts
```

### Entry points

- `.venv/bin/python -m scripts.update_performance_diagrams` renders/synchronizes the committed SVG; `--check` compares canonical output.

### Composition roots

- `render_diagrams()` in `scripts/update_performance_diagrams.py` owns generated-chart composition.

### Public API / exports

- Not applicable; this is a private documentation-generation tool and SVG.

### Tests / CI / benchmarks

- `tests/test_performance_diagrams.py` validates run coverage, chart content, SVG determinism, and stale-file synchronization.
- Existing JSON run artifacts are the source; no benchmark execution was required or performed.

## 4. Canonical ownership map

| Concept | Canonical owner | Writers | Consumers | Derived state | Composition root | Evidence | Status |
|---|---|---|---|---|---|---|---|
| Benchmark measurement | saved benchmark JSON | benchmark harnesses (historical artifacts) | loader | none | `load_shared_harness_runs` | updater:303-387 | one source per run |
| Across-version suite panels | `scripts/update_performance_diagrams.py` | `_shared_harness_panels` | SVG renderer | displayed ratios/cells | `render_diagrams` | updater:578-700 | canonical |
| Existing cross-suite composite | `scripts/update_performance_diagrams.py` | `_composite_performance_row` | SVG renderer | equal-suite geometric mean | `render_diagrams` | updater:596-658 | canonical, already documented |
| Proposed size index | not implemented | none | proposed appended chart | geometric mean of common case ratios per size | should remain `render_diagrams` | current run groups + tests | needs definition/test before implementation |
| Generated artifact | `docs/images/performance-comparison.svg` | updater | Markdown readers | static SVG | `sync_diagrams` | rendered capture | canonical |

## 5. Connection graph

| From | To | Relationship | Symbol/API | Evidence | Runtime relevance | Confidence |
|---|---|---|---|---|---|---|
| `tools/shared-benchmark-results/*.json` | updater | file read | `load_shared_harness_runs` | `scripts/update_performance_diagrams.py:303-387` | build-time/docs only | high |
| paired 2.2.0.4 JSON | updater | file read / append matching suite cases | `load_build_comparison_runs` | updater:370-386 | build-time/docs only | high |
| updater | generated SVG | render/write | `render_diagrams`, `sync_diagrams` | updater:578-593, 1010-1050 | build-time/docs only | high |
| Markdown baseline | generated SVG | image embed | `![...](images/performance-comparison.svg)` | `docs/native-wheel-performance-baseline.md:23` | docs display | high |
| diagram tests | updater/output | contract validation | `DiagramTests`, `BuildComparisonTests` | `tests/test_performance_diagrams.py` | test-time only | high |

### Connection notes

The score implementation, chart rendering, and asset writing already share one
owner. Do not add a second generator or hand-maintained chart values.

## 6. Control-flow / data-flow traces

### Flow A — current chart

```text
saved shared-harness JSON
  -> validate package/suite/config/fixture coverage
  -> append paired 2.2.0.4 native/universal outcomes
  -> make suite heatmap rows and existing composite row
  -> render one SVG
  -> write/check docs/images/performance-comparison.svg
  -> embed in native-wheel-performance-baseline.md
```

### Flow B — candidate size indices (read-only audit calculation)

```text
1k: cluster-1k common positive-p50 cases (9)
30k/100k: cluster-large common positive-p50 cases (6 at each size)
200k: persistence-200k common positive-p50 JSON cases (6)
  -> per case divide p50 by matching 1.8.0.0 p50
  -> geometric mean across common cases
  -> lowest numeric index is fastest observed index within that panel
```

The 30k/100k persistence group is absent for 2.2.0.3. To keep every release
and both 2.2.0.4 builds on each point, it must be excluded at those sizes. At
200k MessagePack StoreState operations are unsupported and excluded. Connect
operations do not carry these fixture sizes and therefore do not belong in the
size score.

## 7. Lifecycle / state map

Not applicable: generated static documentation asset; no runtime lifecycle.

## 8. Cache / index / invalidation map

Not applicable: no runtime cache. SVG is regenerated from saved JSON and checked
byte-for-byte by `--check`.

## 9. Evidence ledger

### E-001 — current chart generator and current coverage

- **Source:** `scripts/update_performance_diagrams.py:303-387, 578-700`
- **Symbol:** `load_shared_harness_runs`, `_shared_harness_panels`, `render_diagrams`
- **Observation:** shared groups are cluster-1k, cluster-large, cluster-scaling, connect, persistence-30k/100k/200k; the build pair is appended for each suite. The current chart keeps all those suite-specific panels.
- **Supports:** workload coverage and dataflow.
- **Confidence:** high.

### E-002 — current aggregation contract

- **Source:** `scripts/update_performance_diagrams.py:596-658`
- **Symbol:** `_composite_performance_row`
- **Observation:** current score is geometric mean of matched per-case p50 ratios, with cluster state, persistence, connect equally weighted; cases without positive measured values for all columns are skipped.
- **Supports:** recommended per-size formula can reuse established mechanics, but requires a different per-size suite mix and explicit counts.
- **Confidence:** high.

### E-003 — matched case counts and exploratory indices

- **Source:** read-only invocation of current loader over local JSON; no files changed and no benchmark run.
- **Observation:** common cases by size are 1k=9, 30k=6, 100k=6, 200k=6. Indices (columns in current chart order) were:

| Size | 1.8.0.0 | 1.9.0.1 | 2.0.0.0 | 2.0.0.2 | 2.2.0.0 | 2.2.0.1 | 2.2.0.3 | 2.2.0.4 native | 2.2.0.4 universal |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 1k | 1.0000 | 1.0476 | 0.9016 | 0.8594 | 0.9313 | 0.9199 | 0.8473 | **0.7638** | 1.0377 |
| 30k | 1.0000 | 0.9677 | 0.8343 | 0.8068 | 0.8174 | 0.8528 | 0.8059 | **0.7831** | 1.0775 |
| 100k | 1.0000 | 0.9941 | 0.8590 | 0.8386 | 0.8199 | 0.8242 | 0.7840 | **0.7771** | 0.9297 |
| 200k | **1.0000** | 1.0782 | 1.0437 | 1.0565 | 1.0481 | 1.0930 | 1.0594 | 1.1039 | 1.0075 |

- **Supports:** if plotted with this exact candidate formula, the lowest observed point is 2.2.0.4 native for 1k/30k/100k and 1.8.0.0 for 200k. This is descriptive only.
- **Confidence:** high for the arithmetic/data selection; low for any generalized ranking.

### E-004 — existing SVG rendered evidence

- **Source:** `evidence/screenshots/before/900px-chart.png` (Firefox headless render; 1397×3114 capture)
- **Observation:** existing grid and labels are legible at the captured size; chart is tall and dense. A new graph will increase total document height. No narrow-viewport or assistive-technology capture was made.
- **Supports:** medium-confidence observation of current visual density only.
- **Confidence:** medium.

### E-005 — independent opposition

- **Source:** `reports/ui-opposition-report.md`
- **Observation:** critic flags connected-line scaling implication, winner overclaim, and lack of uncertainty representation; recommends four separate point panels/dot matrix and explicit case mix/counts.
- **Supports:** chart format should be changed from the approved mockup before implementation.
- **Confidence:** high for semantic risk.

## 10. Repository history / MCP evidence

No additional history/context was necessary. The current local tree is authoritative.

## 11. External research ledger

Not applicable. No external semantics or standards were needed to inspect the
measurement data and rendered SVG.

## 12. Confirmed findings

### F-001 — Workload mix changes across size points; connected trend is misleading

- **Type:** DOCS / visualization correctness
- **Priority:** P1 (high risk of misinterpretation)
- **Confidence:** HIGH
- **Affected:** proposed addition to `scripts/update_performance_diagrams.py` and `docs/images/performance-comparison.svg`
- **Observed behavior:** 1k point summarizes 9 cluster cases; 30k/100k points summarize 6 cluster-large cases each; 200k point summarizes 6 JSON persistence cases. This does not form a same-workload scaling series.
- **Evidence:** E-001, E-003, E-004, E-005.
- **Reproduction:** reproduced by loading current saved JSON and intersecting positive-p50 `case_id`s across all nine displayed columns.
- **Root cause:** available runs differ by suite, fixture size, and coverage; 2.2.0.3 has no 30k/100k persistence run; 200k MessagePack is unsupported.
- **Canonical owner:** benchmark aggregation and chart generation in `scripts/update_performance_diagrams.py`.
- **Impact:** line connections and “best” language can overstate scaling comparability and imply a universal winner.
- **Compatibility:** no public API; visual/documentation contract only.
- **Security/performance boundary:** no runtime behavior; no benchmarks rerun.
- **Recommended direction:** keep all nine entries but present four unconnected per-size point panels using one documented normalized index and display the case count/suite mix in each heading. Say “lowest observed index in this panel.”
- **Do not:** connect points across sizes, call the series a full-workload scaling curve, rank releases universally, or infer significance/causality.
- **Future regression tests:** assert exact group and case selection for each size; assert common positive p50 across all nine entries; assert score math against a synthetic fixture; assert omitted unsupported/unmeasured cases and panel labels; assert no line-connecting SVG polylines across sizes.

### F-002 — “Best” should be qualified as a metric-specific panel minimum

- **Type:** DOCS
- **Priority:** P2
- **Confidence:** HIGH
- **Affected:** chart annotations and baseline copy.
- **Observed behavior:** candidate geometric means produce minima at native 2.2.0.4 for 1k/30k/100k, but 1.8.0.0 at 200k; the fourth point uses a different suite.
- **Evidence:** E-003.
- **Reproduction:** read-only current-loader computation.
- **Root cause:** equal-case geometric means answer “which has the lowest normalized median across this panel’s selected cases,” not all workloads/deployments.
- **Canonical owner:** score function plus accompanying methodology text.
- **Impact:** unqualified “best” can be misunderstood as a universal release recommendation.
- **Compatibility:** none.
- **Security/performance boundary:** descriptive benchmark statistic only.
- **Recommended direction:** label the minimum “lowest observed index” and explain lower is faster, per-size case mix, p50/config caveats.
- **Do not:** make confidence/significance claims from the point minimum.
- **Future regression tests:** chart labels/text retain the qualification and show non-fabricated missing status.

## 13. Unverified hypotheses / open questions

| Hypothesis | Why plausible | Missing evidence | How to verify | Priority |
|---|---|---|---|---|
| The four panels will remain readable when embedded on narrow Markdown viewports. | Existing SVG is intrinsically 1385px wide and already dense. | 375px and 768px rendered captures of the new panel design. | Render the appended chart at three widths and inspect text/overflow before accepting visual quality. | P2 |
| A full accessible data alternative is needed beyond the current SVG image label. | SVG has `role=img`, title, and aria-label, but detailed data cells are primarily visual/tooltip content. | Screen-reader/DOM inspection. | Add a concise text/table summary only if needed and validate the accessible tree. | P3 |
| Some per-size score cases may come from different warmup/repetition configs. | Existing docs already state older cluster runs differ from 2.2.0.4 configurations. | Per-point configuration inventory and displayed note. | Derive config summary from metadata when implementing; do not hide differences. | P2 |

## 14. Areas explicitly healthy / no action

- Existing heatmap data remain panel-specific, explicit N/A cells and p50 ratios are preserved.
- `render_diagrams()` is already the chart owner; no second renderer or new dependency is warranted.
- No historical benchmarks need to be rerun; saved JSON supports a descriptive per-size intersection.
- Existing raw results, prior charts history in Git, and version columns remain untouched by this audit.

## 15. Candidate ranking

| Candidate | Type | Correctness | Dup authority | Boundary leak | Lifecycle/state | Security | Testability | Perf | Compat risk | Complexity | Recommendation |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---|
| Add per-size matched index and unconnected dot panels to current updater | docs/data visualization | 4 | 0 | 0 | 0 | 0 | 4 | 0 | 1 | 2 | Proceed only after user accepts opposition modification and methodology |
| Keep connected line graph from mockup | data visualization | 2 | 0 | 0 | 0 | 0 | 2 | 0 | 1 | 1 | Reject: implies a continuous comparable workload trend |
| Rerun missing historical 2.2.0.3 persistence | benchmark | 0 | 0 | 0 | 0 | 0 | 1 | 0 | 2 | N/A | Do not: historical-wheel reruns were previously prohibited; omit missing suite instead |

## 16. Dependency-ordered implementation plan

### PLAN-001 — Add size-specific matched case indices and unconnected point panels

- **Type:** docs / tests / benchmark visualization
- **Priority:** P1
- **Primary target:** `scripts/update_performance_diagrams.py`
- **Audit alongside:** `tests/test_performance_diagrams.py`, `docs/native-wheel-performance-baseline.md`
- **Depends on:** user accepts the revised unconnected-panel format.
- **Current problem:** no per-size summary exists; approved mockup connects non-equivalent points and says “best.”
- **Root cause:** per-size available suite/case coverage differs.
- **Desired invariant:** each plotted entry at a given size is scored from the same positive measured cases and baseline; no across-size connection implies same workload; existing panels remain unchanged.
- **Implementation direction:** add a size-specific scorer using cluster-1k for 1k, cluster-large at 30k/100k, and JSON persistence-200k at 200k. Use geometric mean of per-case p50 ratios against 1.8.0.0 with equal case weight. Show case counts, suite labels, 2.2.0.4 native/universal distinctly, and panel minimum as “lowest observed index.”
- **Do not:** alter prior data, rerun historical wheels, include unsupported cases as zero, or make a universal winner claim.
- **Test-first plan:** synthetic runs with varied p50s; exact size/group coverage; exclusion of missing/unsupported cases; invalid/no-common-workload failure; reference=1 invariance; deterministic labels and no connecting polylines.
- **Required tests:** focused `tests.test_performance_diagrams`, full suite, Ruff/format, Pyright, Mypy; existing two unrelated Mypy unused-ignore baseline errors should be reported.
- **Performance check:** not applicable; static chart generation over bounded saved JSON.
- **Compatibility:** no published API or schema changes; preserve existing SVG panels and reference entries.
- **Security notes:** no sensitive data in chart; only static benchmark p50s.
- **Acceptance criteria:** existing heatmap panels are byte-equivalent except output height/layout additions; four per-size panels include correct common-case counts and all nine points; winner wording is qualified; benchmark/config caveats appear; SVG XML and refresh `--check` pass.
- **Estimated scope:** medium.
- **Stop condition:** stop once the appended panels and copy meet criteria; no benchmark reruns or unrelated chart redesign.

## 17. Items explicitly not to implement

- Do not change the existing suite panels or prior composite.
- Do not edit the user’s current benchmark/runtime changes.
- Do not connect size points or label a universal “best version.”
- Do not rerun historical wheels or alter saved run artifacts.
- Do not commit/remove the `.ui`/`.superpowers` audit/mockup folders without user direction.

## 18. Validation performed

### Tests

- `.venv/bin/python -m unittest tests.test_performance_diagrams`: 16 tests passed against the current working tree.
- This focused suite does not validate the proposed new size-panel implementation because no production or test code was changed.

### Static analysis

- Inspected generator, test ownership, documentation, raw run metadata and coverage.
- Existing chart SVG parsed/rendered in Firefox headless to `evidence/screenshots/before/900px-chart.png`.
- `git diff --check` passed; user changes remain untouched.

### Build/compile

- Not applicable; no code/build changes.

### Benchmarks

- None run. Only existing benchmark JSON was read.

### Diagnostics/reproductions

- Read-only score computation over saved data produced E-003; no output files or benchmark data were modified.

## 19. Limitations

- The rendered evidence covers the current SVG at one large viewport, not the proposed chart or smaller viewports.
- No automated screen-reader/a11y scan was run; current SVG exposes an image name, not a structured data table.
- Existing runs differ in some warmup/repetition configurations; native/universal runs were sequential. Scores are descriptive, not causal or statistical-significance claims.
- The uncommitted source changes may alter benchmark harness semantics but do not change the saved inputs used for this chart audit. They were deliberately not reviewed or modified beyond noting their presence.

## Stop / continue decision

Audit is complete. The revised point-panel design is safer than the connected
line mockup. **Pause before implementation** until the user confirms the
opposition modification: no connecting lines, per-size workload/case counts,
and “lowest observed index” instead of “best.”

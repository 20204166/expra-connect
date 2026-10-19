# Latest-build pairwise performance diagram

## Purpose

Add a case-level, unweighted diagram that makes it easy to compare the two
newest complete native/universal benchmark versions. Keep this diagnostic view
separate from the existing across-release weighted score and winner summary.

## Proposed behavior

- Discover the two newest versions with complete native and universal results
  for cluster state, persistence, and connect.
- Render one SVG with two directional panels:
  - **Latest native:** latest native versus previous native, and latest native
    versus latest universal.
  - **Latest universal:** latest universal versus previous universal, and
    latest universal versus latest native.
- Include every case ID present in both sides of a comparison. Display each
  case's p50 measurements and the candidate/reference ratio; ratios below
  `1×` mean the candidate is faster, and ratios above `1×` mean it is slower.
- Show unsupported outcomes as unsupported, and omit cases missing from either
  side. Do not invent latency values or penalties.
- Include cases from cluster state, connect, and persistence; visually separate
  suites and retain operation/size labels.
- State that comparisons are unweighted and do not feed the existing weighted
  score, summary winner, or historical comparison window.
- Identify differences in benchmark configuration in the chart or an adjacent
  note. In particular, the current 2.2.0.4 and 2.2.0.5 cluster-state runs use
  different warmup/repetition counts, so those version-to-version results are
  descriptive rather than controlled comparisons.

## Data flow and ownership

- Reuse the existing build-result loader and `BenchmarkRun` / `BenchmarkOutcome`
  models in `scripts/update_performance_diagrams.py`.
- Add a distinct generated SVG artifact for this pairwise view; do not alter the
  existing native-versus-universal per-version charts or weighted data path.
- Include the artifact in the normal refresh and `--check` freshness workflow.
- Render from persisted benchmark JSON only. Generation remains deterministic
  and does not execute benchmark workloads.

## Validation

- Unit tests select the newest two complete paired versions and verify the
  four directional comparisons and ratio semantics.
- Tests cover missing cases, unsupported results, differing configuration
  metadata, incomplete version pairs, and deterministic SVG output.
- Verify that the weighted index inputs and summary remain unchanged.
- Refresh the SVG, run the generator in `--check` mode, and run the repository's
  required unit, lint, formatting, and type checks.

## Out of scope

- Changing benchmark collection or rerunning benchmarks.
- Adding these pairwise comparisons to the weighted winner score.
- Drawing conclusions about sustained performance, stability, or other
  hardware from these p50 results.

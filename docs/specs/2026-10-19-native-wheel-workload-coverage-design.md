# Native-wheel workload coverage expansion design

## Goal

Increase the native/universal release baseline from 74 to 89 distinct benchmark
cases (about 20%) by measuring deterministic operations from currently
underrepresented library features and presenting the expanded, accurately
qualified coverage in `docs/native-wheel-performance-baseline.md`.

The 20% target means **more measured workload cases**, not a 20% speedup, 20%
of APIs, or 20% code coverage.

## Current context

- `docs/native-wheel-performance-baseline.md` reports 74 distinct cases across
  cluster state, persistence, and connect. It explicitly does not claim coverage
  of every public API, live discovery, socket/TLS, or concurrency.
- `scripts/benchmark_release_wheels.py` benchmarks native/universal wheel
  artifacts through the shared benchmark support and validates module
  provenance.
- Existing result JSON is immutable: a complete saved suite is reused and is
  never overwritten. The 2.2.1.0 native and universal wheels and their existing
  result files are available locally.
- `scripts/update_performance_diagrams.py` validates saved result metadata,
  builds visible history, reports coverage, and produces the SVGs embedded in
  the baseline page.
- Current checkout includes unrelated, uncommitted changes in
  `src/expra_connect/cluster/roles.py` and `tests/test_role_hardening.py`.
  They must remain untouched by this work.

## Proposed workload matrix

Add exactly 15 case IDs, organized as five feature slices. Fixture setup is
outside the timed operation where the shared measurement helper supports it.
Use fixed clocks, stable synthetic values, and no live network, random fixture
construction, or concurrent load in the measured region.

| Feature slice | Cases | Owning workload suite | Shape |
|---|---:|---|---|
| Discovery | 3 | connect | candidate validation; endpoint selection/ranking over a fixed route set; registry insert/expiry with an injected clock |
| Pairing/trust | 3 | connect | relationship projection; trusted-candidate classification with unchanged transport; classification of a valid next-generation proof using a fixed key fixture |
| Capability/surface authorization | 3 | connect | authorized generic capability handler; peer-granted surface dispatch; cluster-granted surface dispatch |
| Cluster policy | 3 | cluster_state | role authorization (`RoleState.allows`) on the existing 1k, 30k, and 100k fixtures, with a fixed actor/target/grant arrangement |
| Profile migration | 3 | persistence | end-to-end synthetic directory migration at 1, 16, and 64 files, using the existing fixture shape |

The benchmark operations must call the package's canonical public or established
subsystem API. Do not duplicate production algorithms inside a benchmark. Pairing
fixtures may pre-create a deterministic identity/root and valid proof outside
the measured region. Setup must restore mutable registries/caches before every
sample, and stateful operations must not accidentally measure cumulative growth.

## Result storage and compatibility

1. Extend the existing benchmark suites so future full release runs include the
   new cases in their normal suite result files.
2. For the already-published 2.2.1.0 wheels, produce **separate supplemental
   result files** for native and universal artifacts. Do not replace, append to,
   or regenerate the existing 2.2.1.0 suite files.
3. The diagram updater recognizes supplements and merges their outcomes with
   their matching immutable version/flavor/suite result in memory only.
4. Validate supplement provenance (package version, suite, wheel flavor,
   imported module paths, interpreter/platform), exact fixture and operation
   parity between native/universal supplements, unique case IDs, and compatible
   measurement configuration. Reject conflicting duplicates; do not select a
   winner silently.
5. When a build is archived out of the visible history window, archive its
   supplemental JSON beside the corresponding original results. Never delete
   raw measurements.
6. Older releases remain explicitly unmeasured for new cases. They receive no
   fabricated latency result or penalty. Existing matched-case scoring rules
   remain unchanged.

## Baseline page and generated diagrams

- Update `docs/native-wheel-performance-baseline.md` with the 89-case inventory,
  a feature-to-workload coverage summary, supplemental run dates/provenance,
  updated per-version measured/total counts, and explicit unmeasured/unsupported
  caveats.
- Refresh only the generated performance SVGs through
  `scripts/update_performance_diagrams.py`; keep the updater as the canonical
  chart owner and do not hand-edit SVG output.
- Do not change historical measurements or claim a performance improvement
  solely because coverage increased.
- Explain that 2.2.1.0 has the existing 69 measured cases plus the 15 new
  supplemental cases (84/89 per flavor if all new runs validate and complete);
  older builds' coverage denominator grows while their measured numerators do
  not. Verify actual generated counts rather than hard-coding assumed output.

## Validation and acceptance

- Add tests first for every added operation's output/fixture metadata, selected
  operation behavior, stable setup, and exact workload count.
- Add updater tests for valid supplement merging; duplicate/conflicting IDs;
  mismatched suite/version/flavor/runtime/config; missing native/universal
  supplements; provenance mismatch; archive association; and preserving the
  original result bytes.
- Run the new feature cases from the built 2.2.1.0 native and universal wheel
  artifacts, with matching interpreter, platform, warmup, and repetition
  configuration. Retain raw samples and checksums; do not rebuild the wheels.
- Verify the expanded coverage inventory is exactly the observed case union and
  that all latency ratios still use only matched positive-p50 cases.
- Run the diagram updater tests, updater write mode, updater `--check`, SVG XML
  validation, `git diff --check`, and the repository's normal lint/type/test
  checks for changed Python code.
- Acceptance requires 15 new unique case IDs, 89 total distinct cases, separate
  immutable supplements for both 2.2.1.0 wheel flavors, passing provenance and
  pair validation, accurate baseline counts, and unchanged original result
  files.

## Non-goals

- No 20% latency target or production performance claim.
- No live LAN discovery, socket/TLS handshake, multi-process stress, or
  concurrent load in this deterministic expansion.
- No coverage percentage based on source lines, symbols, or API count.
- No wheel rebuild, version bump, modification of historical raw result files,
  broad benchmark framework rewrite, or security/durability weakening.
- No edits to the pre-existing `cluster/roles.py` and
  `tests/test_role_hardening.py` changes.

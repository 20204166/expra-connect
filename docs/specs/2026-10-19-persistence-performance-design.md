# Persistence performance optimization design

## Goal

Improve persistence performance for representative 30k, 100k, and 200k workloads
without regressing the existing 2.2.0.5 performance profile. Compare the latest
baseline with 1.8.0.0 and the two preceding releases, 2.2.0.4 and 2.2.0.3.

## Scope and constraints

- Profile measured persistence operations before changing implementation.
- Optimize only bottlenecks demonstrated by the benchmark data; prefer localized
  changes over a persistence-format replacement.
- Preserve logical JSON/YAML compatibility, MessagePack limits and validation, atomic
  replacement, private file permissions, fsync behavior, migration semantics,
  and fail-closed handling of malformed state.
- Do not weaken durability or delete or rewrite existing persisted state as an
  optimization.
- Compact JSON separators are approved for measurement and may be adopted only
  if sorted-key determinism and compatibility remain intact and benchmarks show
  repeatable gains; the whitespace and exact bytes may change.
- Keep unrelated cluster, connect, and other existing improvements unchanged.

## Benchmark targets and acceptance

- Measure the full persistence suite at 30k, 100k, and 200k, including every
  operation the suite currently supports at each size. Respect the established
  rule that 200k MessagePack StateStore is unsupported and do not substitute
  codec-only measurements.
- Use matched CPython/platform/configuration runs where possible. Record p50 and
  the full sample distribution for the current implementation and candidate.
- Require a repeatable improvement in at least one targeted persistence workload.
- Treat any repeatable slowdown in any existing measured operation versus
  2.2.0.5 as a release blocker; repeat apparent regressions to separate noise
  from a real effect. Do not claim zero regression based on one noisy run.
- Run the full test suite, Ruff, Pyright, Mypy, and the complete benchmark matrix
  before considering the optimization complete.

## Implementation boundary

Likely ownership is `src/expra_connect/persistence.py`, with focused tests in
`tests/test_persistence.py`. The benchmark source is
`scripts/benchmark_persistence.py`; immutable release-wheel results live under
`docs/performance-build-comparison-results/`. Modify benchmark infrastructure
only if needed to make measurements matched and reproducible, and retain the
existing benchmark result schema and release artifact workflow.

## Risks

Serialization and filesystem timings are noisy and environment-sensitive.
Native and universal wheels may respond differently to Python-level changes.
Optimizations to cloning, encoding, or atomic writes can affect aliasing,
validation, crash consistency, security permissions, and error behavior; tests
must cover those contracts before performance claims are accepted.

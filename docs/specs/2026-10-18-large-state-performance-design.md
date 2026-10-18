# Large-State Performance Design

## Goal

Improve measured performance for large cluster-role state conversion and JSON
state loading, using 500,000 entries as the stress workload and 100,000 entries
as the final comparison point, without changing behavior or causing repeatable
regressions at 30,000 entries or in other tracked benchmark operations.

## Scope and order

Work is sequential and measurement-gated:

1. **Cluster and role state:** profile role encode/decode, cluster encode/decode,
   and round-trip. The current benchmark lives in
   `scripts/benchmark_cluster_state.py`; canonical conversion owners are
   `src/expra_connect/cluster/state.py` and
   `src/expra_connect/cluster/operations.py`.
2. **JSON persistence:** profile file read, JSON parse, root validation,
   migration when applicable, and full `JsonStateStore.load`. The canonical
   owner is `src/expra_connect/persistence.py`; the existing focused harness is
   `scripts/benchmark_persistence.py` and currently covers 30k/100k fixtures.
3. **Request signing / verification / replay:** retain as a non-scaling control
   metric and split its stages only if a measured issue warrants it. Its current
   `peer_count` benchmark argument does not traverse that many peers, so it is
   not a 500k-entry workload.

The MessagePack store has a 1 MiB payload and 100,000-element decoder limits;
do not raise or bypass these limits to create a 500k benchmark. JSON can use a
separately sized 500k fixture if resource checks show the run is safe.

## Measurement and optimization rules

- Establish same-checkout, same-runtime baselines before each change. Record
  Python/platform, source import paths, fixture shape and payload bytes, warmup
  and repetition counts, median, and sample range. Do not compare source runs
  directly with historical native-wheel timings.
- Profile each target first. Name the measured bottleneck and change only one
  cause at a time; retain no change whose improvement is not repeatable.
- Run the relevant target at 30k, 100k, and 500k when memory and input limits
  permit. If the full 500k fixture is unsafe, use smaller isolated operations
  only when their shape remains representative, label the limitation, and do
  not claim full-workload 500k results.
- After each retained change, re-run the owning 30k/100k benchmark set and
  other tracked benchmark controls. Treat differences within sample noise as
  inconclusive; require no repeatable regression before accepting a gain.
- Preserve persisted and wire representations, validation/error behavior,
  ordering, migrations, replay/security controls, and durability semantics.
  Do not trade correctness, compatibility, or durability for speed.
- Update `docs/performance-next-targets.md` and
  `docs/native-wheel-performance-baseline.md` with exact run context and results;
  preserve prior measurements.

## Verification

Add test-first regression coverage for any changed behavior boundary, then run
the focused tests for the touched subsystem, its relevant broader tests, and the
repository's configured lint/type checks. For Python code changes, load and
apply `senior-python-engineering` before the first write. Validate the final
source-checkout benchmark on 30k and 100k, plus the safe 500k stress workload;
report any resource-limited or inconclusive measurements explicitly. No wheel
release or public schema change is part of this work.

## Non-goals

- Increasing MessagePack size or element-count limits.
- Changing serialized formats or role/capability semantics.
- Optimizing unrelated request signing or replay behavior without evidence.
- Claiming a specific speedup before profiling and repeatable measurements.

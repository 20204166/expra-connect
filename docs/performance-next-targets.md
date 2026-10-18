# Next Performance Targets

Use this note to focus the next performance investigation. The full wheel
history, including the 2.2.0.3 run, is in
[native-wheel-performance-baseline.md](native-wheel-performance-baseline.md).
For routine follow-up, measure the current wheel only and compare against that
wheel's recorded baseline; do not rerun every historical wheel unless a
regression or compatibility question specifically needs that comparison.

## Priority targets

| Target | 2.2.0.3 measured p50 | Comparison / why investigate |
|---|---:|---|
| Cluster-state round-trip | 642,052.580 µs at 30k; 2,392,156.492 µs at 100k | Versus saved 2.2.0.1 results: 20.0% faster at 30k, 5.5% slower at 100k. The size-dependent result still warrants profiling before changing serialization. |
| Role-state decode | 447,365.681 µs at 30k; 1,622,517.737 µs at 100k | Versus saved 2.2.0.1 results: 6.9% faster at 30k, 9.9% slower at 100k. The 100k result does not confirm an improvement; profile the large fixture before further edits. |
| Request sign + verify + replay | 44.193 µs at 30k; 53.024 µs at 100k | Versus saved 2.2.0.1 results: 29.6% faster at 30k and 2.1% faster at 100k. This combines three stages; the `peer_count` parameter is scalar and does not process all 30k/100k entries. |
| JSON store load, full workload | 12,653.623 µs at 30k; 33,198.291 µs at 100k | Versus saved 2.2.0.1 full-workload results: 46.7% faster at 30k, 19.4% slower at 100k. Earlier focused-load measurements use a different harness context; do not conflate them. |

These are investigation priorities, not proven bottlenecks or guaranteed
optimization opportunities. The 100k persistence fixture uses compact
`[integer, boolean]` pairs, unlike the 30k dictionary-record fixture. Only the
2.2.0.3 wheel was run for this update; prior results were not rerun. The 100k
session-population stage was slower in this run than in the saved 2.2.0.1 run,
so treat cross-version differences as noisy signals, not controlled attribution.

## Other areas to monitor

These are secondary candidates, not current optimization recommendations:

- **JSON store save:** about 135 ms at 30k and 228 ms at 100k in the 2.2.0.1
  full workload. This is the largest wall-clock persistence cost, but focused
  results are noisy and saves intentionally include flush, file sync, atomic
  replacement, and directory sync. Only investigate if the product explicitly
  accepts a durability tradeoff or profiling shows avoidable time outside the
  required durability work.
- **Role lookup at 100k:** 1.940 µs in 2.2.0.1 versus 1.806 µs in 2.2.0.0.
  It is a small absolute cost and much faster than the 1.x wheels due to the
  newer indexed lookup implementation. Monitor for a repeatable regression;
  do not prioritize based on this one small difference.
- **MessagePack store load and decode:** a matched source-checkout benchmark
  (Python 3.12.3, 2 warmups, 9 single-operation samples, 100k compact pairs)
  measured 25.7 ms for store load, 25.9 ms for decode, and 0.7 µs for root
  validation. File reads were about 52 µs after warmup, so decoding dominates
  this source run. The earlier 17.6 ms full-workload result came from a native
  Cython wheel, while the focused result used a different harness/runtime
  context; this run therefore does not establish a like-for-like regression.
  Keep the benchmark stages aligned and compare source with source or native
  wheel with the same exact wheel before treating this as a code hotspot. A
  decoder-option probe on the same fixture measured bounded `unpackb` at
  22.5 ms, default limits at 24.6 ms, and tuple output at 18.3 ms; tuple output
  changes the returned list semantics, and none of these approaches the 5 ms
  target. The current C-backed MessagePack decoder is already doing the bulk
  of the work, so reaching 5 ms without changing representation or output
  semantics is not supported by these measurements.
- **Capability-grant lookup and session checks/resume:** these remain around
  1–3 µs in the 2.2.0.1 full workloads. They are low-priority unless a
  different production workload shows them to be frequent or costly.
- **Codec-only JSON/MessagePack encode and decode:** useful diagnostic stages,
  but the latest wheel does not show a consistent broad regression relative to
  2.2.0.0. Keep them as supporting measurements for persistence work rather
  than independent targets for now.

The best next work remains the four priority areas above. Revisit these
secondary candidates only when focused measurements reproduce a meaningful
cost or a real workload makes them important.

## Owning implementation and tests

- **Cluster-state round-trip:** `src/expra_connect/cluster/operations.py`
  (`ClusterState.to_dict` / `ClusterState.from_dict`) and
  `src/expra_connect/cluster/state.py` (`role_state_to_dict` /
  `role_state_from_dict`). Start with `tests/test_cluster.py`,
  `tests/test_cluster_persistence.py`, `tests/test_role_engine.py`, and
  `tests/test_role_hardening.py`.
- **Role-state decode:** `src/expra_connect/cluster/state.py`, especially
  `role_state_from_dict`; cluster deserialization calls it from
  `ClusterState.from_dict` in `operations.py`. Tests:
  `tests/test_role_engine.py`, `tests/test_role_hardening.py`, and
  `tests/test_canonical_cluster.py`.
- **Request signing, verification, replay:**
  `src/expra_connect/remote/wire_protocol.py` (`_canonical`, `_signature`,
  `sign_request`, `verify_request`, and `ReplayCache`). Tests:
  `tests/test_wire_protocol.py`, `tests/test_protocol.py`, and
  `tests/test_remote_service.py`. Preserve canonical signing, constant-time
  signature comparison, freshness checks, replay rejection, bounded cache
  behavior, and thread safety.
- **JSON store load:** `src/expra_connect/persistence.py`
  (`JsonStateStore.load`; optionally `migrate_state` when a store has a kind).
  Tests: `tests/test_persistence.py`. Keep corruption handling, object-root
  validation, migration behavior, and error wrapping intact.

## Next measurement scope

Use one current native wheel and the same machine/runtime as the baseline.
Retain the 30k and 100k fixtures where valid, repeat focused operations enough
to report median and sample range, and record payload shape, bytes, and imported
module paths. Split the combined request measurement into sign-only,
verify-only, and replay-cache work. Split cluster round-trip into encode and
decode, while retaining an end-to-end round-trip number. For JSON load, report
file read, `json.loads`, and store validation separately as well as the full
`JsonStateStore.load` operation. Keep durability-sensitive save measurements
out of this target set unless save becomes a specific concern.

Do not weaken validation, security, persistence durability, or compatibility
to improve a timing. Make a change only after profiling identifies its cost,
add regression tests for preserved behavior, and rerun the same focused suite
against the resulting wheel. Avoid claiming a speedup from a single noisy
sample or from comparisons across different release source code.

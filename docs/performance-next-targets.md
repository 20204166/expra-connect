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

These earlier full-workload priorities are investigation leads, not proven
bottlenecks or guaranteed optimization opportunities. The 100k persistence
fixture uses compact `[integer, boolean]` pairs, unlike the 30k dictionary-record
fixture. Only the 2.2.0.3 wheel was run for the historical update summarized
above; saved 2.2.0.1 results were not rerun then. The 100k session-population
stage was slower in that run than the saved 2.2.0.1 result, so treat the
difference as a noisy signal, not controlled attribution.

## Follow-up from the shared-harness comparison

The following is a separate, matched-harness view of 2.2.0.3 versus 2.2.0.1; it
does not replace or merge with the historical full-workload priorities above.
Each pair uses the same operation, fixture, and sampling configuration within
its suite. Values are p50 microseconds per operation; ratios below 1× are lower
latency. These are cross-version measurements, not source-identical A/B tests.

| Shared-harness stage | Fixture | 2.2.0.3 p50 | 2.2.0.1 p50 | Ratio |
|---|---:|---:|---:|---:|
| Role-state decode | 100k assignments/grants | 1,555,487.998 µs | 1,604,481.567 µs | 0.969× |
| Cluster encode | 100k assignments/grants | 648,875.038 µs | 632,284.528 µs | 1.03× |
| Cluster round-trip (full cluster run) | 100k assignments/grants | 2,032,573.385 µs | 2,128,261.628 µs | 0.955× |
| Request sign + verify + replay | ping request | 48.001 µs | 45.686 µs | 1.05× |
| JSON store load | 200k compact pairs | 75,766.272 µs | 73,407.054 µs | 1.03× |
| JSON store save | 200k compact pairs | 433,646.930 µs | 495,830.952 µs | 0.875× |

The cluster rows used one warmup and three samples; connect used ten warmups
and fifty samples; persistence used two warmups and nine samples. Allocation
tracking was disabled. The shared-harness diagram in the baseline page shows
exact p50s and ratios, not raw distributions; sample distributions remain in
the local benchmark files. The 2.2.0.3 30k/100k persistence cases remain
intentionally unmeasured, so no such comparison is inferred.

Earlier isolated cluster-scaling runs produced different p50s: at 30k,
862,397.939 µs for 2.2.0.3 versus 709,260.875 µs for 2.2.0.1 (1.22×); at 100k,
2,560,372.569 µs versus 2,668,379.242 µs (0.960×). Keep those historical
results separate because their run context differs from both the full cluster
run above and the controlled repeat below.

## Controlled follow-up run (2026-10-18)

The cluster round-trip was rerun with the same isolated operation, fixture,
interpreter, warmups, repetitions, and batch size for both native wheels. Both
imported the expected Cython modules. Values are p50 microseconds; ranges show
the seven measured samples, with allocation tracking disabled.

| Fixture | Payload | 2.2.0.1 p50 (range) | 2.2.0.3 p50 (range) | p50 ratio |
|---|---:|---:|---:|---:|
| 30k assignments + grants | 7,497,059 B | 725,882.566 (629,769.583–862,258.502) µs | 644,566.340 (557,020.454–720,983.099) µs | 0.888× |
| 100k assignments + grants | 25,067,059 B | 2,822,026.417 (2,219,431.874–3,189,748.713) µs | 2,246,649.959 (1,959,623.114–2,367,111.153) µs | 0.796× |

Command for each wheel: `scripts/benchmark_cluster_state.py --sizes 30000
100000 --warmups 2 --repetitions 7 --batch-count 1
--skip-allocation-tracking --operation 'cluster round-trip'`. Python was 3.12.3
on Linux x86_64/glibc 2.39. The 2.2.0.3 wheel SHA-256 was
`13fa79d5d2f8eb4ec60f7331e69ec8964be2d574bd0ec841e53ecd356697faab`.
The sample ranges overlap at both sizes and are broad at 100k; treat the lower
2.2.0.3 medians as a directional repeat, not a causal speedup or proof that the
earlier run discrepancy is fully resolved.

A one-sample `cProfile` diagnostic on the source checkout at 30k attributed
most measured operation time to `ClusterState.from_dict` / `role_state_from_dict`
and `to_dict` / `role_state_to_dict`. Profiling increased the timed operation
from subsecond wheel measurements to about 2.1 seconds, and includes Python
instrumentation; it identifies the broad serialization/validation stages only,
not a reliable native-wheel hotspot or safe optimization. No code change is
justified by this profile alone.

### Updated follow-up order

1. **Keep cluster serialization under observation; do not optimize speculatively.**
   The controlled repeat has lower 2.2.0.3 medians, but overlapping sample
   ranges and the source-only profile do not isolate an avoidable cost. If this
   path matters in production, repeat under a quiet host and profile the exact
   native wheel with a profiler that can see its compiled code.
2. **Keep 200k JSON load as a decode-focused diagnostic.** In the current-wheel
   run, p50s were 6.695 ms for file read, 80.649 ms for JSON decode, 0.371 µs
   for root validation, and 78.587 ms for full store load. These independent
   stage medians are not additive. Parsing is the dominant measured stage, but
   do not alter input validation or output semantics without a targeted profile
   and correctness proof.
3. **Repeat request verification before prioritizing it.** On 2.2.0.3, one
   50-sample diagnostic measured 33.714 µs for sign-only, 69.155 µs for the
   existing combined sign + verify + replay operation, and 9.523 µs for replay
   cache alone. This combined-path result differs from the earlier 48.001 µs
   p50 and has a wide range (40.657–101.101 µs); it is not a pure verify-only
   measurement and does not establish a regression. Repeat in a quiet run and
   separate authentication from replay only with a semantics-preserving harness.
4. **Do not tune JSON save as part of this follow-up.** The current-wheel 200k
   p50 was 438.013 ms, but the operation includes required durability work.
   Preserve flush, file sync, atomic replacement, and directory sync.

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

The four priorities above preserve the earlier full-workload investigation
record. For the next follow-up, use the separately labeled shared-harness order
below; revisit secondary candidates only when focused measurements reproduce a
meaningful cost or a real workload makes them important.

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

## Recommended order

1. **Establish a repeatable current-wheel baseline.** Record the exact wheel
   filename and checksum, Python/runtime and platform, benchmark command,
   fixture dimensions and shapes, warmups, sample count, median, and range.
   Confirm the benchmark imports the installed wheel rather than the checkout.
2. **Attribute the cluster and role-state costs.** Measure serialization and
   deserialization separately at both sizes, alongside the end-to-end
   round-trip. Keep the same fixture shape between versions and sizes where
   the format permits it; call out any unavoidable shape difference.
3. **Split request work into its component stages.** Measure signing,
   verification, and replay-cache lookup independently, then retain the
   combined operation as a cross-check. Use representative request sizes and
   preserve the existing signature, freshness, replay, cache-bound, and
   concurrency behavior.
4. **Attribute JSON load.** Measure file read, JSON parsing, store-level
   validation/migration, and the full load with identical input bytes. Keep
   corruption and invalid-root cases in correctness validation, not in the
   timing samples.
5. **Profile before proposing an optimization.** Explain the measured work
   responsible for a repeatable cost and estimate whether it matters to a real
   workload. If no avoidable cost is identified, retain the current behavior
   and record the result instead of optimizing for a target number alone.

For each candidate change, compare the same focused benchmark before and
after on the same machine/runtime, and run its owning tests plus the relevant
broader suite. Report absolute timings and sample spread as well as relative
change. Reject a speedup if it depends on changing serialized output, weakening
integrity/durability checks, or omitting work performed by the public API.

## Measurement record

Append a dated result to
[`native-wheel-performance-baseline.md`](native-wheel-performance-baseline.md)
when a new wheel baseline is established. Include enough metadata to reproduce
the run and compare like-for-like:

- wheel filename, SHA-256, Python version, platform, and benchmark revision;
- command, import path, warmup count, measured sample count, median, and range;
- operation boundaries, fixture sizes and shapes, and serialized byte counts;
- whether the run is native-wheel or source-checkout, kept in separate
  comparison groups;
- correctness checks run and any deviations, noise, or environment limits.

Do not replace prior measurements: preserve historical rows and label new
results with their exact wheel and harness context. A result from a different
fixture, interpreter, import path, or operation boundary is diagnostic only,
not evidence of a regression or improvement.

# Native and universal wheel performance baseline

**Measured:** 18 October 2026<br>
**Runtime:** CPython 3.12.3, Linux x86_64, glibc 2.39<br>
**Wheels:** 1.8.0.0 universal, 1.9.0.1 universal, 2.0.0.0 native,
2.0.0.2 native, and 2.2.0.0 native.

This is a local microbenchmark baseline for deciding which areas merit a
separate investigation. It is not an end-to-end or concurrent-load benchmark.
Times below are median p50 microseconds per operation; lower is faster.

## Full representative workload, 30,000 entries

The workload covered identity fingerprint/create, role and capability lookup,
role-state decode, cluster-state round-trip, a registry populated with 30,000
logical sessions, session check/resume, request sign/verify/replay, and JSON/
MessagePack persistence. The role/capability and cluster fixtures each contain
30,000 assignments/grants. Session checks cycle through the populated registry.

| Workload (p50 µs) | 1.8.0.0 | 1.9.0.1 | 2.0.0.0 | 2.0.0.2 | 2.2.0.0 |
|---|---:|---:|---:|---:|---:|
| Identity fingerprint | 3.488 | 3.493 | 3.637 | 3.526 | 3.468 |
| Identity create | 85.945 | 85.619 | 89.312 | 88.753 | 95.112 |
| Role lookup, 30k | 2,583.840 | 2,996.315 | 0.878 | 1.099 | 1.100 |
| Capability-grant lookup, 30k | 3,436.184 | 4,517.772 | 3.429 | 2.951 | 2.833 |
| Role-state decode, 30k | 548,989.626 | 529,225.886 | 476,690.313 | 453,534.355 | 466,598.152 |
| Cluster-state round-trip, 30k | 793,627.243 | 811,085.022 | 666,801.551 | 726,923.119 | 704,112.025 |
| Session current-check, 30k registry | 1.588 | 1.614 | 1.407 | 1.480 | 1.366 |
| Resume existing session, 30k registry | 1.113 | 1.139 | 1.033 | 1.070 | 0.961 |
| Request sign + verify + replay | 51.417 | 55.526 | 50.430 | 49.628 | 53.855 |
| JSON save, 30k | 118,370.601 | 111,097.027 | 118,505.228 | 120,661.689 | 121,545.071 |
| JSON load, 30k | 14,739.032 | 15,358.149 | 13,613.861 | 13,257.649 | 15,649.717 |
| MessagePack save, 30k | 17,621.295 | 18,164.789 | 17,058.112 | 17,614.781 | 18,597.323 |
| MessagePack load, 30k | 13,337.801 | 14,000.981 | 11,176.847 | 12,802.411 | 15,137.381 |

### Full-workload observations

- Role lookup is dramatically faster in the 2.x wheels than in 1.8/1.9. This
  primarily reflects the newer indexed lookup implementation, not Cython alone.
- Role-state decode and cluster-state round-trip are faster in the 2.x builds
  than in 1.8/1.9, with 2.0.0.2 the fastest decode result in this run.
- The 2.2.0.0 wheel is fastest for session current-check and resume in this
  run. These are tiny timings and should be rechecked before acting on small
  differences.
- Relative to 1.8.0.0, 2.2.0.0 was slower for request sign/verify/replay and
  for the measured JSON and MessagePack loads/saves. The absolute persistence
  differences are small relative to filesystem timing variation and are not
  stable optimization evidence yet.
- Identity fingerprint timings are effectively tied. Identity creation was
  slower in 2.2.0.0 than 1.8.0.0 in this run.

## Focused persistence measurements

For the 30k case, the payload is a dictionary containing 30,000
`{"id": "node-N", "online": bool}` records (JSON: 1,143,915 bytes;
MessagePack: 678,909 bytes). This matches the data shape used by the full
workload. For 100k, the MessagePack store's 1 MiB payload limit prevents using
100,000 such dictionaries. The 100k case therefore uses compact `[integer,
boolean]` pairs (JSON: 1,538,915 bytes; MessagePack: 568,565 bytes). The 100k
figures are valid store operations, but their record shape differs from the
30k case and should not be treated as a direct same-shape scaling curve.

“Encode only” is in-memory `json.dumps` / `msgpack.packb`; “decode only” is
in-memory `json.loads` / bounded `msgpack.unpackb`. Store load includes reading
the file and the store's validation; store save includes the atomic write,
flush, `fsync`, replace, and directory sync. These are p50 microseconds.

| Records | Codec / operation (p50 µs) | 1.8.0.0 | 1.9.0.1 | 2.0.0.0 | 2.0.0.2 | 2.2.0.0 |
|---:|---|---:|---:|---:|---:|---:|
| 30k | JSON encode only | 22,153.805 | 17,240.886 | 18,161.170 | 19,481.879 | 19,195.559 |
| 30k | JSON decode only | 16,348.053 | 17,950.683 | 16,247.453 | 15,267.566 | 15,441.194 |
| 30k | JSON store load | 20,439.023 | 19,405.515 | 17,792.806 | 20,600.808 | 17,483.783 |
| 30k | JSON store save | 133,082.986 | 141,038.569 | 147,078.061 | 135,221.323 | 129,821.966 |
| 30k | MessagePack encode only | 5,779.663 | 6,380.697 | 7,701.716 | 6,768.858 | 5,944.233 |
| 30k | MessagePack decode only | 13,766.898 | 14,641.075 | 15,931.067 | 14,842.876 | 15,541.341 |
| 30k | MessagePack store load | 15,295.279 | 15,083.399 | 17,397.220 | 17,668.862 | 15,562.592 |
| 30k | MessagePack store save | 17,604.350 | 17,569.024 | 19,231.017 | 19,404.363 | 18,163.534 |
| 100k | JSON encode only | 28,044.418 | 27,062.007 | 25,647.330 | 31,254.809 | 31,635.446 |
| 100k | JSON decode only | 33,821.527 | 36,022.806 | 33,107.190 | 42,717.563 | 39,422.062 |
| 100k | JSON store load | 35,864.478 | 42,373.465 | 34,532.366 | 44,580.455 | 42,282.433 |
| 100k | JSON store save | 227,287.983 | 237,015.486 | 234,750.836 | 240,928.117 | 266,137.800 |
| 100k | MessagePack encode only | 16,243.576 | 16,134.760 | 14,512.012 | 14,491.974 | 18,018.681 |
| 100k | MessagePack decode only | 27,431.663 | 27,280.298 | 24,953.672 | 25,184.297 | 26,322.112 |
| 100k | MessagePack store load | 26,863.742 | 27,133.261 | 23,834.453 | 25,578.500 | 25,172.544 |
| 100k | MessagePack store save | 29,494.712 | 27,511.623 | 66,451.977 | 28,922.913 | 27,576.416 |

Focused timings use two warmups and the median of nine individual measurements.
The 2.0.0.0 100k MessagePack save result is an outlier: its nine samples ranged
from 29,264.607 to 127,773.796 µs, indicating filesystem noise rather than a
credible codec regression. JSON saves also vary substantially because the
store performs durability syncs.

### Persistence interpretation

- Cythonizing the package persistence wrapper would not compile or accelerate
  the `json`, `msgpack`, or operating-system implementations used for the heavy
  work. Persistence remains Python in all five wheels; the 2.x native wheels
  compile cluster and remote modules, not `expra_connect.persistence`.
- MessagePack encode is consistently much faster than JSON encode for these
  fixtures. MessagePack save is much faster than JSON save because the payload
  is smaller and writes less data, although `fsync` makes absolute save times
  noisy.
- Decode-only and store-load numbers are close enough across versions that
  there is no convincing evidence of a wheel-version or Cython advantage for
  persistence. The 2.2.0.0 full-workload JSON and MessagePack loads were slower
  than 1.8.0.0, but the focused measurements show substantial run-to-run
  variation; repeat under a controlled disk/CPU environment before selecting
  an optimization target.

## Method and limitations

- Full workload: one process per wheel, warmup followed by repeated batches;
  table entries are the median p50 per-call batch result. Small identity and
  wire workloads use fewer calls per batch than the large state workloads.
- Focused persistence: same CPython interpreter, shared dependency
  environment, isolated temporary files, two warmups, nine timed calls; table
  reports the median. Save measurements include storage sync and are therefore
  sensitive to filesystem/device state.
- 1.8.0.0 and 1.9.0.1 are universal wheels; 2.0.0.0, 2.0.0.2, and 2.2.0.0
  are CPython 3.12 Linux native wheels. Compiled module selection differs by
  version; imported module paths were checked during each run.
- These are different releases with code changes, so comparisons do not
  isolate Cython's effect. Only a source-identical Python/native pair can
  isolate compilation, and persistence is not among the compiled modules in
  these wheels.
- The full suite is representative, not exhaustive: it does not measure live
  network discovery, socket transport, TLS, concurrency/load behavior, or
  every public API.

## Raw run logs

The command outputs used for this report are retained in `/tmp/opencode/` for
this workspace session (`bench_full_30k_<version>.txt` and
`bench_persistence_<version>.txt`). They are temporary and are not part of the
repository.

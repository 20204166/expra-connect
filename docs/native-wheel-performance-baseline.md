# Native and universal wheel performance baseline

**Measured:** 18 October 2026<br>
**Runtime:** CPython 3.12.3, Linux x86_64, glibc 2.39<br>
**Wheels:** 1.8.0.0 and 1.9.0.1 universal; 2.0.0.0, 2.0.0.2, 2.2.0.0, 2.2.0.1, and 2.2.0.3 native CPython 3.12 wheels.

This is a local sequential microbenchmark baseline, not an end-to-end or concurrent-load benchmark. The full-workload tables report p50 microseconds per operation. Focused persistence tables report p50 microseconds per operation; lower is faster.

## Full representative workload, 30,000 entries

The workload covers identity fingerprint/create, role and capability lookup, role-state decode, cluster-state round-trip, a registry populated with 30,000 logical sessions, session check/resume, request sign/verify/replay, and JSON/MessagePack store save/load. Role, capability, cluster, and session fixtures are sized to 30,000 entries.

| Workload (p50 µs; 30,000 entries) | 1.8.0.0 | 1.9.0.1 | 2.0.0.0 | 2.0.0.2 | 2.2.0.0 | 2.2.0.1 | 2.2.0.3 |
|---|---:|---:|---:|---:|---:|---:|---:|
| Identity fingerprint | 3.256 | 3.537 | 3.789 | 4.211 | 3.563 | 3.300 | 3.490 |
| Identity create | 83.133 | 94.132 | 99.336 | 109.011 | 92.157 | 90.689 | 84.880 |
| Role lookup | 2,430.987 | 2,808.370 | 1.161 | 1.161 | 0.955 | 1.186 | 1.059 |
| Capability-grant lookup | 3,335.050 | 3,804.662 | 3.074 | 2.928 | 3.422 | 3.221 | 2.942 |
| Role-state decode | 464,627.777 | 540,068.771 | 451,065.873 | 481,705.967 | 488,668.713 | 480,475.087 | 447,365.681 |
| Cluster-state round-trip | 699,320.301 | 818,616.883 | 652,355.504 | 714,902.643 | 762,628.068 | 802,603.921 | 642,052.580 |
| Session current-check | 1.211 | 1.302 | 1.403 | 1.614 | 1.577 | 1.751 | 1.244 |
| Resume existing session | 1.068 | 0.960 | 1.078 | 0.993 | 1.071 | 1.148 | 1.046 |
| Request sign + verify + replay | 44.436 | 47.511 | 56.454 | 54.287 | 52.310 | 62.771 | 44.193 |
| JSON store save | 113,000.029 | 129,407.163 | 112,063.263 | 112,563.589 | 130,529.877 | 135,322.533 | 131,900.272 |
| JSON store load | 12,435.259 | 13,272.046 | 13,357.540 | 13,327.416 | 12,837.649 | 23,758.722 | 12,653.623 |
| MessagePack store save | 16,956.794 | 19,012.447 | 21,145.209 | 18,802.714 | 19,055.725 | 19,314.700 | 17,346.147 |
| MessagePack store load | 11,852.171 | 12,638.863 | 12,960.181 | 11,192.322 | 13,136.111 | 11,728.387 | 10,635.092 |

## Full representative workload, 100,000 entries

The same workload is scaled to 100,000 role assignments, capability grants, cluster entries, and logical sessions. To stay within MessagePackStateStore's 1 MiB payload limit, the persistence portion uses compact `[integer, boolean]` peer pairs at 100k rather than the 30k dictionary records. Thus, this is a 100k full workload, but persistence fixture shape differs from the 30k run.

| Workload (p50 µs; 100,000 entries) | 1.8.0.0 | 1.9.0.1 | 2.0.0.0 | 2.0.0.2 | 2.2.0.0 | 2.2.0.1 | 2.2.0.3 |
|---|---:|---:|---:|---:|---:|---:|---:|
| Identity fingerprint | 3.616 | 4.316 | 3.754 | 3.781 | 4.115 | 3.371 | 3.659 |
| Identity create | 86.892 | 129.402 | 91.097 | 96.894 | 112.585 | 91.273 | 98.909 |
| Role lookup | 8,779.286 | 10,230.796 | 1.318 | 1.340 | 1.806 | 1.940 | 1.570 |
| Capability-grant lookup | 12,663.549 | 12,684.377 | 2.997 | 4.281 | 3.743 | 2.935 | 3.453 |
| Role-state decode | 1,820,453.843 | 1,785,313.437 | 1,578,170.643 | 1,456,092.223 | 1,592,903.588 | 1,476,089.990 | 1,622,517.737 |
| Cluster-state round-trip | 2,607,906.101 | 2,667,692.423 | 2,393,749.568 | 2,316,625.685 | 2,587,734.183 | 2,267,784.899 | 2,392,156.492 |
| Session current-check | 1.540 | 1.722 | 1.564 | 1.688 | 1.526 | 1.572 | 1.798 |
| Resume existing session | 1.025 | 1.097 | 0.959 | 1.077 | 1.003 | 0.986 | 1.171 |
| Request sign + verify + replay | 46.527 | 53.227 | 49.716 | 57.394 | 52.077 | 54.185 | 53.024 |
| JSON store save | 239,636.592 | 237,956.317 | 199,241.227 | 216,666.995 | 240,493.675 | 228,108.187 | 236,323.555 |
| JSON store load | 29,950.597 | 32,386.252 | 28,859.619 | 28,242.827 | 40,613.558 | 27,797.299 | 33,198.291 |
| MessagePack store save | 27,371.941 | 25,139.101 | 26,265.950 | 26,841.385 | 31,538.610 | 26,173.831 | 33,085.048 |
| MessagePack store load | 19,669.004 | 20,503.942 | 17,541.989 | 16,819.452 | 19,427.443 | 17,592.443 | 28,005.346 |

## 2.2.0.3 comparison notes

Only the 2.2.0.3 wheel was run for this update; all earlier columns are the
saved results above, not new runs. The same full-workload harness and fixture
shapes were used. On this run, role-state decode was 6.9% faster at 30k than
2.2.0.1, but 9.9% slower at 100k. Cluster-state round-trip was 20.0% faster at
30k and 5.5% slower at 100k. These are single-wheel-run comparisons, not a
controlled source-only attribution; in particular, they do not show a decode
improvement at both sizes. The 100k session-population stage took 233.488 s in
2.2.0.3 versus 215.870 s in the saved 2.2.0.1 run, evidence that run-to-run
machine/runtime variation is material. Interpret the broad 100k differences
cautiously and repeat only if a decision depends on them.

## Full persistence operation baseline

Focused persistence workloads cover all operations, not just load/decode. At 30k, the payload is a map containing 30,000 `{"id": "node-N", "online": bool}` records (JSON: 1,143,915 bytes; MessagePack: 678,909 bytes). At 100k, the MessagePack limit requires a map containing 100,000 compact `[integer, boolean]` pairs (JSON: 1,538,915 bytes; MessagePack: 568,565 bytes). The 100k figures are valid store operations but are not a same-shape scaling curve from 30k.

“Encode only” is in-memory `json.dumps` / `msgpack.packb`; “decode only” is in-memory `json.loads` / bounded `msgpack.unpackb`. Store load includes file read and validation. Store save includes atomic write, flush, file `fsync`, replace, and directory sync.

### 30,000 dictionary records

| Codec / operation (30k; p50 µs) | 1.8.0.0 | 1.9.0.1 | 2.0.0.0 | 2.0.0.2 | 2.2.0.0 | 2.2.0.1 |
|---|---:|---:|---:|---:|---:|---:|
| JSON encode only | 15,690.631 | 16,160.734 | 15,950.240 | 17,312.031 | 24,016.649 | 19,382.736 |
| JSON decode only | 13,551.643 | 14,617.806 | 14,069.144 | 15,297.424 | 18,701.765 | 17,880.416 |
| JSON store load | 15,775.211 | 16,576.208 | 16,035.951 | 17,854.743 | 22,016.851 | 21,762.911 |
| JSON store save | 105,583.542 | 111,448.343 | 108,063.985 | 113,479.724 | 147,894.132 | 137,569.686 |
| MessagePack encode only | 5,796.296 | 6,854.832 | 5,589.193 | 6,801.396 | 7,091.781 | 7,795.617 |
| MessagePack decode only | 12,561.879 | 12,376.712 | 11,667.799 | 12,506.506 | 15,329.703 | 15,366.140 |
| MessagePack store load | 14,522.070 | 14,254.501 | 12,873.271 | 14,353.956 | 17,650.244 | 16,235.186 |
| MessagePack store save | 17,772.051 | 18,519.361 | 18,250.645 | 19,606.303 | 22,319.932 | 20,015.609 |

### 100,000 compact-pair records

| Codec / operation (100k; p50 µs) | 1.8.0.0 | 1.9.0.1 | 2.0.0.0 | 2.0.0.2 | 2.2.0.0 | 2.2.0.1 |
|---|---:|---:|---:|---:|---:|---:|
| JSON encode only | 23,696.880 | 26,203.316 | 28,836.204 | 30,637.709 | 31,730.282 | 29,643.477 |
| JSON decode only | 30,044.354 | 34,136.732 | 34,602.200 | 36,410.807 | 32,609.809 | 35,046.396 |
| JSON store load | 36,096.924 | 35,333.567 | 37,272.994 | 39,372.257 | 35,150.676 | 39,976.003 |
| JSON store save | 232,074.701 | 232,111.345 | 231,738.618 | 266,629.545 | 252,297.804 | 233,810.730 |
| MessagePack encode only | 14,783.072 | 13,266.308 | 14,164.053 | 15,119.409 | 13,300.168 | 14,168.914 |
| MessagePack decode only | 22,735.279 | 22,789.210 | 22,105.349 | 21,868.756 | 25,467.753 | 24,268.310 |
| MessagePack store load | 21,552.181 | 22,304.657 | 22,345.191 | 25,357.790 | 25,308.263 | 24,148.464 |
| MessagePack store save | 23,962.951 | 25,839.671 | 26,691.241 | 28,029.595 | 31,498.214 | 27,673.055 |

Focused measurements used two warmups and nine timed operations per cell; the table reports the median. Raw logs also retain minimum/maximum samples. Full-workload timings use the harness's per-workload warmups and repeated batches; exact batch counts are in the saved harness script. The six historical wheel results were run sequentially; 2.2.0.3 was measured separately using the same harness and fixture shapes. Save timings remain sensitive to filesystem/device state.

## Interpretation and limitations

- The 2.2.0.1 wheel is the first wheel in this comparison that includes `expra_connect.persistence` as a compiled Cython extension. Cythonizing the wrapper does not compile `json`, `msgpack`, or operating-system filesystem operations.
- The 2.2.0.0-to-2.2.0.1 comparison is not a source-identical Cython A/B test: source changed, and the new release adds persistence to the compiled module set. Do not attribute timing differences solely to Cython.
- The 2.2.0.1 results are mixed: for example, some 100k full-workload state operations were faster than 2.2.0.0, while role lookup and request signing were slower. Focused 100k JSON store-load results also differ materially from the full-workload load results, despite using the same compact fixture; treat filesystem/cache variation and per-harness sampling as significant limitations.
- The 2.2.0.3 full-workload results are also mixed against 2.2.0.1: role-state decode and cluster round-trip improved at 30k, but were slower at 100k. The 100k session-population stage was slower as well, so the single current-wheel run cannot establish that the larger-input changes are regressions or attribute differences to one code change.
- The focused measurements show mixed persistence results by codec, operation, and fixture size. Save timings include filesystem sync noise, and differences among release sources mean the results do not establish a stable general speedup.
- The MessagePack payloads are much smaller than corresponding JSON payloads for these fixtures. Their store-save timings include durability syncs and are not codec-only costs.
- These representative workloads do not measure live network discovery, socket transport, TLS, concurrent load, or every public API.

## Raw run logs

The six historical full and focused logs are retained outside the repository in `/tmp/opencode/bench-comparison-2.2.0.1/` (`full_30k_<version>.txt`, `full_100k_<version>.txt`, and `persistence_<version>.txt`). The 2.2.0.3 full logs are in `/tmp/opencode/bench-comparison-2.2.0.3/` (`full_30k_2.2.0.3.txt` and `full_100k_2.2.0.3.txt`). The temporary harness copies are also in the historical log directory; they are not part of the package or wheel.

# Shared-harness benchmark results

These structured runs were produced by the consolidated standalone harnesses.
They are independent from the historical logs under
`tools/bench-comparison-200k-all-wheels/` and are not interchangeable with
those measurements.

## Coverage

- Cluster state: full operation set at 1,000 assignments/grants, 2 warmups,
  9 samples, all seven package versions; the full operation set at 30k/100k
  uses one warmup and 3 samples with YAML disabled. Cluster round-trip scaling
  at 30k/100k uses one warmup and 3 samples, all seven versions.
- Connect: all available stages, 10 warmups and 50 samples, all seven versions.
- Persistence: 30,000 and 100,000 entries for the six earlier versions;
  200,000 JSON entries for all seven versions. Persistence uses 2 warmups and
  9 samples.
- MessagePack StateStore at 200,000 entries is recorded as unsupported for all
  versions. No codec-only 200,000 measurements were substituted.
- Persistence at 30,000/100,000 entries for 2.2.0.3 was not run. The recorded
  2.2.0.3 200,000 run was the only approved persistence size for that version.
- Connect YAML StateStore stages are unavailable for 1.8.0.0 because that API
  is absent. Other versions record those stages as measured.

Every JSON file contains raw samples, full distribution summaries, per-case
fixture/size/payload/config metadata, package version, and imported module paths.
Each has a readable `.txt` companion. The cluster results use a 1,000-entry
fixture; the earlier 100-entry cross-version commands were smoke tests only and
are not part of this result set. Allocation tracing was disabled for these
comparisons (`peak_bytes` is null); the helper's isolated allocation pass is
tested separately from timing.

## Reproduction

Run each command from the repository root with the intended extracted package
first on `PYTHONPATH`, for example:

```sh
PYTHONPATH=/path/to/1.8.0.0 .venv/bin/python -m scripts.benchmark_cluster_state \
  --sizes 1000 --warmups 2 --repetitions 9 --skip-allocation-tracking \
  --json-output tools/shared-benchmark-results/cluster-1k-1.8.0.0.json

PYTHONPATH=/path/to/1.8.0.0 .venv/bin/python -m scripts.benchmark_persistence \
  --sizes 30000 --warmups 2 --repetitions 9 \
  --json-output tools/shared-benchmark-results/persistence-30k-1.8.0.0.json

PYTHONPATH=/path/to/1.8.0.0 .venv/bin/python -m tools.connect_performance_benchmark \
  --warmups 10 --repetitions 50 \
  --json-output tools/shared-benchmark-results/connect-1.8.0.0.json
```

For persistence, also run sizes 100000 for the six earlier versions and size
200000 for all seven versions. Do not create 30k/100k results for 2.2.0.3.

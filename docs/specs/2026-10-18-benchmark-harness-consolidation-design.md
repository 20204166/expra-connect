# Benchmark harness consolidation design

## Goal

Improve benchmark comparability and usefulness without duplicating measurement
logic or replacing the existing historical baseline. Reuse the strongest
features already present in the cluster-state and persistence benchmark scripts
and produce machine-readable evidence that can be reviewed and selectively
consumed by the performance baseline.

## Approved approach

Add a small shared measurement core used by the existing domain-specific
scripts. Keep fixture construction, operation definitions, domain limits, and
compatibility decisions in `benchmark_cluster_state.py` and
`benchmark_persistence.py`; do not make a monolithic registry of unrelated
operations and do not add benchmark dependencies.

The shared core owns deterministic timing and result reporting:

- warmups and repeated samples, reporting raw samples and p50/min/max;
- optional batching with per-operation normalized timings;
- optional peak traced allocation in an independent pass, never inside the
  timed samples;
- run metadata (benchmark/schema version, Python/platform, package version,
  operation, fixture metadata, warmups/repetitions/batch count);
- imported module paths as provenance;
- readable text output plus a versioned JSON result format, with unsupported
  operations represented explicitly and accompanied by reasons.

Use the same validated measurement configuration and result structure in both
scripts. Preserve domain-specific CLI options where they have meaning, such as
the YAML size cap and persistence codec limits. Reject invalid sample counts,
batch counts, and fixture sizes before timing.

## Measurements and compatibility

Use the seven available extracted versions (1.8.0.0, 1.9.0.1, 2.0.0.0,
2.0.0.2, 2.2.0.0, 2.2.0.1, and 2.2.0.3) for new comparable runs. Run the
staged role/cluster-state benchmark on its established sizes (1k, 30k, 100k),
including role and cluster encode/decode, cluster round trips, persisted
cluster-document round trips, and YAML stages only through the configured safe
size. Its fixture must continue to model one role assignment and one capability
grant per node. Run focused persistence at 30k, 100k, and 200k where each
operation is supported, preserving the documented fixture shape for each size.
Include matched encode/decode, file-read, root-validation, StateStore load, and
StateStore save stages for JSON and MessagePack when the store accepts the
fixture. At 200k, measure JSON encode/decode and StateStore load/save, and mark
MessagePack StateStore operations unsupported by their payload/array limits.

Before comparing any operation across versions, verify the imported package
version and module path, characterize API/fixture compatibility, and ensure the
operation has the same meaning and serialized shape. Unsupported or
incompatible cases remain explicit gaps with reasons; never substitute another
operation or silently mix fixture shapes. Existing historical measurements are
immutable and must not be overwritten. New runs identify their harness version
and remain separately labeled until validated for inclusion in the canonical
baseline JSON and diagrams.

## Results and documentation

Store one raw structured result per version and benchmark suite alongside the
existing raw logs. Preserve readable logs as useful review artifacts, but do
not parse human-formatted output as the canonical source. Record fixture shape,
payload bytes, size, and any measurement caveats. The baseline importer must
validate schema, version, operation coverage, provenance, and unavailable
reasons before adding values; diagrams continue to show p50 ratios and exact
latency values in tooltips. Current-checkout-only diagnostics, if any, remain
separate from seven-version comparisons.

## Validation

- Unit-test shared timing (sample count, batch normalization, allocation pass,
  and invalid configuration) with deterministic fake operations or a clock seam.
- Test JSON serialization and strict result-schema validation, including
  unsupported reasons and provenance.
- Test both benchmark scripts retain their domain-specific operations, fixture
  semantics, limits, and operation selection after adopting the shared core.
- Run at least one small cross-version smoke measurement before full runs;
  verify all seven versions and module paths, then run complete requested suites
  sequentially and retain raw output.
- Confirm outputs are deterministic apart from measurements and run metadata,
  existing historical JSON values are unchanged, and all repository checks pass.

## Out of scope

- Changes to production serialization, persistence, cluster, or runtime code.
- Relaxing MessagePack size/array limits or measuring unsupported store paths by
  bypassing those limits.
- Treating current-checkout measurements as historical cross-version results.
- Replacing the historical baseline with results from a different fixture or
  harness without explicit labeling and validation.

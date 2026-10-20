# Persistence performance and benchmark coverage design

## Goal

Improve measured MessagePack store decode/load/save and JSON store save
performance while adding correctness and benchmark coverage for 100k cluster
state and 200k persistence workloads. Preserve existing behavior, safety limits,
benchmark cases, and saved historical results.

## Current evidence

- Source-build persistence benchmark, CPython 3.12.3, Linux x86_64:
  - 100k MessagePack decode: p50 25.4 ms; store load: 23.6 ms; store save:
    46.6 ms.
  - 100k JSON store save: p50 95.1 ms.
  - 200k JSON store save: p50 226.1 ms.
- `MessagePackStateStore.save` packs the value and then unpacks the encoded
  payload to check that the bounded loader will accept it. Its decoder limit is
  1 MiB, with maximum string, binary, array, and map limits of 1 MiB, 1 MiB,
  100k, and 100k respectively. These limits remain unchanged. The 200k
  MessagePack store cases remain explicitly unavailable.
- JSON persistence already measures six operations at 200k using compact-pair
  records. Add a second nested `dictionary_records` JSON fixture at 200k; keep
  the compact-pair fixture and all its measurements.
- Cluster-state has measured role/cluster encode, decode, round-trip, and
  authorization cases at 100k. YAML encode, parse, and cluster round-trip are
  currently excluded by a benchmark-only 30k YAML size cap. A one-sample 100k
  YAML cluster round-trip completed in 44.2 seconds with a 21.3 MB YAML
  payload. Raise only that benchmark cap to 100k and add all three YAML cases.
- Existing correctness coverage exercises cluster hydration through 200k, but
  does not currently exercise the 100k YAML cluster round-trip or large JSON
  persistence fixture end to end.

## Design

### Persistence optimizations

1. Profile and benchmark the MessagePack store decode/load/save paths on the
   existing 30k and 100k fixtures. Evaluate a bounded structural-validation
   path for save to avoid redundant work only if it preserves the current
   decoder-acceptance contract. Keep strict map-key behavior, payload and
   container limits, error conversion, root-map validation, and atomic write
   ordering unchanged.
2. Profile JSON save at 100k and 200k and optimize only the measured hot path.
   Preserve sorted compact JSON, finite-number rejection, string-only map keys,
   error behavior, and atomic durability.
3. Keep a candidate only when repeated end-to-end benchmark distributions show
   a material improvement in the requested operations. Retest all existing
   persistence operations and representative cluster-state workloads; reject
   changes that materially regress other existing cases. Do not add runtime
   dependencies or weaken limits to obtain a speedup.

### Additive coverage

1. Keep every existing cluster-state size, operation, and YAML cap behavior at
   sizes through 30k. Extend the benchmark-only YAML cap to 100k and measure
   YAML encode, YAML parse, and YAML cluster round-trip at 100k.
2. Keep every existing compact-pairs persistence case. Add a distinct 200k
   `dictionary_records` JSON fixture and measure all existing JSON stages:
   encode, file read, decode, object-root validation, store load, and store
   save. Preserve explicit unavailable outcomes for the 200k MessagePack store
   operations under the existing caps.
3. Add correctness tests for the 100k YAML cluster round-trip and the new 200k
   JSON fixture's saved/loaded value. Add coverage tests proving both old and
   new cases appear in benchmark output and that unsupported MessagePack cases
   remain visible rather than disappearing.
4. Write new benchmark results additively. Do not overwrite or delete prior
   release results. Benchmark the new cases on supported native/universal
   recent comparison wheels where available; keep unavailable/mismatched
   historical cases explicit rather than treating them as regressions.

## Validation and acceptance

- Existing persistence and cluster-state tests continue to pass, with new
  correctness coverage for the added workloads.
- New benchmark records preserve unique case IDs, fixture shape, size,
  provenance, and existing measurement configuration semantics.
- MessagePack limits and unsupported 200k outcomes remain unchanged.
- All pre-existing benchmark cases and saved result files remain present.
- Repeated p50/p95 results demonstrate a real improvement to each retained
  optimization; no unrelated existing workload has a material regression.
- Regenerate the performance summary and diagrams and verify their freshness,
  then run the full unittest, Ruff, Pyright, and Mypy checks.

## Risks and trade-offs

- MessagePack save currently decodes its freshly encoded payload to validate
  decoder compatibility. Replacing that work is safe only if the new validation
  is behaviorally equivalent for nested keys, arrays/maps, binary and extension
  values, recursion, malformed values, and cycles.
- The 100k YAML cluster workload has a large serialized payload and high runtime;
  it increases release benchmark duration. It is opt-in only through the
  existing configured benchmark path, not a new package runtime behavior.
- Adding a second 200k JSON shape expands coverage denominators. Historical runs
  without that shape must remain labelled as unavailable, and the summary must
  not imply that their absent measurements are performance regressions.

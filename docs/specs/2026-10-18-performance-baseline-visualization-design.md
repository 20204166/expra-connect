# Performance baseline visualization redesign

## Goal

Make the performance baseline easy to scan without showing a multiplier as if
it were the measured latency or presenting two competing copies of the same
results. Preserve all recorded measurements, clearly distinguish full-workload
and focused-persistence harnesses, and keep the diagrams reproducibly
updateable.

## Decisions

- Use a versioned JSON file as the single canonical source for recorded
  measurements. Markdown numeric tables will be removed from the baseline page;
  the SVGs are generated views, not the data source.
- Retain the current four-diagram structure and update all diagrams to show
  the exact p50 measurement in microseconds as the primary cell label. Keep the
  relative-to-row-fastest multiplier as a smaller secondary cue, and retain a
  visible relative-performance legend. Tooltips may repeat fixture details but
  must not be the only way to discover the measurement.
- The all-workload overview and persistence view must expose the full-run
  sections—not just JSON and MessagePack. Group the full-run metrics under
  identity, lookups, state, sessions, requests, and persistence. Keep focused
  persistence rows in a separately named panel so harnesses and fixture shapes
  are not conflated.
- Use the already-recorded 2.2.0.3 full-workload JSON and MessagePack save/load
  results at 30k and 100k. Do not label these as focused-persistence results.
  Focused persistence remains unavailable for 2.2.0.3; no focused run is in
  scope.
- Keep 200k MessagePack StateStore save/load unavailable: the existing fixture
  exceeds the store's payload and array limits. Do not change those limits or
  add a separate codec-only run. JSON 200k and the other existing full-workload
  measurements remain as recorded.
- Remove the old numeric benchmark tables and pairwise comparison matrices
  from the Markdown page to avoid duplicate/conflicting presentation. Preserve
  all recorded measurements in JSON; derive pairwise values from the canonical
  full-workload data if a later view needs them rather than keeping a second
  copied table.

## Canonical measurement data

Add a machine-readable JSON file under `docs/` containing:

- run metadata: measurement date, Python version, platform, and wheel versions;
- full-workload p50 values keyed by fixture size, metric, and version;
- focused-persistence p50 values keyed by fixture size, operation, and version;
- explicit `null` cells for unsupported or unmeasured values, with a reason
  field distinguishing unsupported 200k MessagePack store operations from the
  unmeasured 2.2.0.3 focused-persistence rows;
- fixture/harness notes that explain the 100k compact-pair fixture and the
  200k seeded-session-registry measurement.

Move values exactly from the existing tables. Do not recompute, round, or
otherwise change recorded values during migration. A migration test must compare
the JSON values against the current Markdown tables before those tables are
removed (or use a checked-in expected digest/value inventory in the test).

## Diagrams and documentation

Keep four deterministic standalone SVG outputs. All numeric labels include
units and use consistent number formatting; exact p50 is primary and the ratio
is visibly secondary. Relative colors are supplemental, not the sole carrier
of meaning. Show explicit unavailable markers with explanatory reasons.

The persistence view will have distinct panels for full-workload categories
(identity, lookups, state, sessions, requests, JSON/MessagePack persistence)
and focused persistence operations (JSON/MessagePack encode, decode, store
load, and store save). The state-scaling and operation-latency diagrams retain
their specialized views. The overview continues to provide a cross-version
index of full-workload data. Where the same 2.2.0.3 full-run persistence values
appear in both an overview and persistence panel, the labels and captions must
identify them as full-workload results.

The Markdown baseline page will retain explanatory prose, measurement
limitations, raw-log references, and descriptive image alt text, while removing
the old numeric workload tables, focused-persistence tables, and pairwise
matrices. It will identify the JSON file as canonical data and document the
refresh and check commands. Do not claim that the SVGs contain newly measured
results.

## Update workflow and validation

- Update the Python generator to read and validate the JSON data source rather
  than parsing Markdown tables.
- Keep write mode deterministic and `--check` read-only; fail clearly on a
  missing metric, malformed value, unexpected version, or invalid unavailable
  reason.
- Add tests for JSON schema/coverage, exact displayed measurements, secondary
  ratios, explicit gaps/reasons, SVG XML validity, deterministic output, and
  write/check behavior.
- Verify all current measurements are preserved, including 2.2.0.3 full-run
  JSON/MessagePack save/load at 30k and 100k, all 200k N/A cells, and focused
  2.2.0.3 unavailable cells.
- Run the focused diagram tests, updater `--check`, Ruff, Pyright, Mypy, the
  full unittest suite, and `git diff --check`. Report any pre-existing
  repository-wide validation failures separately.

## Out of scope

- No benchmark reruns, including focused 2.2.0.3 persistence or a 200k raw
  MessagePack codec-only measurement.
- No changes to MessagePack payload/array limits or StateStore behavior.
- No fabricated values or mixing of full-workload and focused-persistence
  measurements.

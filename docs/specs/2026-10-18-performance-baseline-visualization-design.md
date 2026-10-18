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
- Retain the current four-diagram structure and keep the existing
  relative `×` labels as the primary cell labels, but normalize each value to
  the matching 1.8.0.0 p50 for the same metric and fixture size. A ratio below
  `1×` means faster than 1.8.0.0; a ratio above `1×` means slower. Improve label
  readability and legend clarity; calculate ratios from source measurements,
  display three significant figures, and do not add `µs` values to cells.
  Preserve exact p50 microseconds in hover tooltips.
- The all-workload overview and persistence view must expose the full-run
  sections—not just JSON and MessagePack. Group the full-run metrics under
  identity, lookups, state, sessions, requests, and persistence. Keep focused
  persistence rows in a separately named panel so harnesses and fixture shapes
  are not conflated.
- Use the already-recorded 2.2.0.3 full-workload JSON and MessagePack save/load
  results at 30k and 100k. Do not label these as focused-persistence results.
  Run the focused persistence harness at 200k for all seven wheels for JSON
  encode/decode/store load/save. The 2.2.0.3 focused rows are therefore measured
  at 200k only; its 30k/100k focused cells remain unavailable.
- Keep 200k MessagePack StateStore save/load unavailable: the existing fixture
  exceeds the store's payload and array limits. Mark focused 200k MessagePack
  operations unsupported for all versions. Do not change those limits or add a
  separate codec-only run. Existing full-workload measurements remain as
  recorded.
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
- focused 30k/100k rows for six measured versions and 200k rows for all seven
  versions, with version coverage explicit in the JSON dataset;
- explicit `null` cells for unsupported or unmeasured values, with a reason
  field distinguishing unsupported 200k MessagePack store operations from the
  unmeasured 2.2.0.3 focused-persistence rows;
- fixture/harness notes that explain the 100k compact-pair fixture, the 200k
  focused compact-pair fixture, and the 200k seeded-session-registry
  full-workload measurement.

Move values exactly from the existing tables. Do not recompute, round, or
otherwise change recorded values during migration. A migration test must compare
the JSON values against the current Markdown tables before those tables are
removed (or use a checked-in expected digest/value inventory in the test).

## Diagrams and documentation

Keep four deterministic standalone SVG outputs. Keep multiplier labels in the
cells, calculated as candidate p50 / 1.8.0.0 p50 for the same metric and size.
Use three significant figures, ensure very small ratios do not round to zero,
and state clearly that below `1×` is faster. Exact p50 microseconds remain
available in tooltips. Relative colors are supplemental, not the sole carrier
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
refresh and check commands. State the ratio formula explicitly: each cell is
the version's p50 divided by the matching 1.8.0.0 p50 for the same metric and
fixture size; `0.7×` therefore means 30% lower p50 latency than 1.8.0.0. Do not
claim that the SVGs contain newly measured results.

## Update workflow and validation

- Update the Python generator to read and validate the JSON data source rather
  than parsing Markdown tables.
- Keep write mode deterministic and `--check` read-only; fail clearly on a
  missing metric, malformed value, unexpected version, or invalid unavailable
  reason.
- Add tests for JSON schema/coverage, ratios against the 1.8.0.0 reference,
  exact p50 tooltip values, explicit gaps/reasons, SVG XML validity,
  deterministic output, and write/check behavior.
- Verify all current measurements are preserved, including 2.2.0.3 full-run
  JSON/MessagePack save/load at 30k and 100k, all 200k MessagePack N/A cells,
  and focused 2.2.0.3 unavailable cells at 30k/100k. Verify the new 200k
  focused JSON measurements and unsupported MessagePack reasons for all wheels.
- Run the focused diagram tests, updater `--check`, Ruff, Pyright, Mypy, the
  full unittest suite, and `git diff --check`. Report any pre-existing
  repository-wide validation failures separately.

## Out of scope

- No reruns of existing measurements or focused persistence at 30k/100k.
- No 200k raw MessagePack codec-only measurement.
- No changes to MessagePack payload/array limits or StateStore behavior.
- No fabricated values or mixing of full-workload and focused-persistence
  measurements.

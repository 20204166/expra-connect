# Host Integration and Profile Migration Design

## Goal

Make Expra Connect easier to embed in host applications and make one-time
legacy-profile conversion safer, without adding host-specific policy or schemas
to the reusable package.

## Scope

- Add a schema-neutral profile migration coordinator that accepts host conversion
  and verification callbacks, stages package profile files, and leaves source
  state unchanged.
- Document lifecycle, host callback/event-loop responsibilities, app-owned
  provider/action boundaries, and profile migration using the public package API.
- Add a small host integration example and benchmark synthetic migration work
  through the existing persistence benchmark harness.
- Preserve the existing pending persistence optimization changes in
  `src/expra_connect/persistence.py`, `tests/test_persistence.py`, and its spec.

## Existing owners and gaps

- `ConnectRuntime` remains the composition/lifecycle owner. A host adapter is
  application-specific and must not be implemented inside this package.
- Host UI/event-loop scheduling, user approval, application data providers,
  process/file action policy, and application call-site cutover stay host-owned.
- `persistence.migrate_state`, `JsonStateStore`, and `YamlStateStore` own
  individual document parsing/migration. The new coordinator must not duplicate
  their schema rules; conversion callbacks use the current serializers/stores.
- There is currently no profile-level staging/verification/promotion helper for
  host-defined multi-file conversion.
- `docs/USER_GUIDE.md` already documents basic runtime usage; extend it with a
  host integration recipe, callback thread/approval caveats, and profile
  conversion workflow rather than creating a second guide.
- `scripts/benchmark_persistence.py` is the canonical benchmark entry point;
  extend its cases and tests rather than creating a parallel harness.

## Proposed API and data flow

Add a focused `src/expra_connect/profile_migration.py` module and export its
supported entry point/types from the package facade. The API will accept:

- an existing source profile directory;
- a destination profile directory that must not already exist;
- a converter receiving a restricted source reader and staged-profile writer;
- a verifier receiving the staged profile path after conversion.

The coordinator will create a private temporary staging directory under the
existing destination parent (same filesystem), expose only validated relative
file operations to the converter, and invoke verification before promotion.
The source remains byte-for-byte untouched. If conversion or verification
fails, only the coordinator-created temporary stage is removed. Promotion is
allowed only when the destination was absent at entry and remains absent at the
promotion check; concurrent migration/activation against the same destination
is unsupported and must be excluded by host lifecycle coordination. The helper
must not claim a cross-platform transaction over multiple files or race-free
coordination it cannot prove.

Reader/writer path validation rejects absolute paths, traversal, empty names,
directories where files are expected, and symlinks. Output files use private
permissions and are written only below the stage. Exceptions and diagnostics
must not include file contents, secrets, or converted values. The host converter
owns schema mapping; the verifier may use `ConnectRuntime` against the staged
profile, then must shut it down before promotion.

The guide/example will show a host-owned queue/scheduler receiving asynchronous
runtime notifications, explicit lifecycle cleanup in `finally`, and a clear
warning that the inbound pairing approval callback is synchronous. A UI host
must use a deadlock-safe approval handoff or reject; discovery/route events may
be queued for its event loop. No Tk, Qt, scanner, or System Analyzer dependency
will be introduced.

## Benchmark

Extend `scripts/benchmark_persistence.py` with synthetic profile conversion
cases at small, medium, and larger file counts. Each case uses a fresh temporary
source/destination, deterministic non-secret fixtures, the same converter and
verifier contract as the public API, and reports migrated file count, total
bytes, and latency distribution via the existing `MeasurementConfig` and JSON
result format. Use no absolute pass/fail latency target: migration is a one-time
operation and no user-facing latency target is established. The benchmark is
evidence for cost/scaling and prevents accidental pathological work; correctness
and source-preservation are asserted by unit tests. Update benchmark help/docs
only where needed, and add the benchmark usage to `USER_GUIDE.md`.

## Failure and safety behavior

- Destination exists, source missing, invalid relative path, converter failure,
  verifier failure, or promotion failure: fail closed; never overwrite source or
  destination.
- Source files remain unchanged and available for rollback after success.
- Stage cleanup is limited to a unique directory created by this invocation.
- Profile migration is an explicit host-controlled operation; it never runs
  implicitly during `ConnectRuntime.start()`.
- The migration callback is trusted host code, not a sandbox. The path wrapper
  constrains its documented file access but does not promise race-free isolation
  against another process modifying the source tree.
- Hosts must stop old runtimes/processes and prevent concurrent migration or
  activation. Package runtime profile locking remains the owner of live profile
  concurrency after activation.

## Testing

- Test source immutability on success and every injected failure.
- Test destination-exists refusal, path traversal/absolute paths/symlinks,
  nested output creation, private output mode where supported, empty conversion,
  verifier ordering, and stage cleanup boundaries.
- Test package runtime can load/validate a representative staged profile and is
  shut down before activation; keep fixtures synthetic and secret-free.
- Test benchmark CLI operation selection, emitted result shape, file/byte
  metadata, and temporary-directory isolation with low sample counts.
- Verify guide/example imports and callback/lifecycle claims against current
  source and tests; run the applicable unit, lint, type, and benchmark checks.

## Non-goals

- No System Analyzer types, field mappings, migration data, UI adapter,
  production startup rewiring, or legacy backend removal in this repository.
- No automatic migration during runtime startup and no deletion of legacy data.
- No promise of multi-file transactional writes, concurrent migration safety,
  or rollback after the host has switched its application configuration.
- No persistence format replacement, performance optimization, or absolute
  migration-latency SLO.

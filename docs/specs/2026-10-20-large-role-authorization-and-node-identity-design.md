# Large-Scale Role Authorization and Node Identity Design

## Goal

Extend role-authorization benchmark evidence to 200,000 cluster records and
make `NodeIdentity.create` reject invalid falsey node-ID arguments such as `0`
instead of silently generating a different identity.

## Existing Ownership

- `scripts/benchmark_cluster_state.py` owns the staged cluster-state benchmark
  fixtures and `RoleState.allows` workload.
- `scripts/benchmark_coverage.py` and `scripts/benchmark_release_wheels.py`
  own workload inventory and release-wheel benchmark orchestration.
- `NodeIdentity.create` in `src/expra_connect/identity/__init__.py` owns
  optional caller-supplied node-ID construction.
- `RoleState.allows` in `src/expra_connect/cluster/roles.py` remains unchanged
  unless separately authorized profiling demonstrates a production defect.

## Design

Add 200,000 to the existing benchmark size choices/defaults and make the
release workload inventory and tests include `200000:role authorization`.
Preserve existing 1k, 30k, and 100k cases, fixture semantics, and output schema.
Verify benchmark fixture consistency and run the 200k authorization workload;
report measurements as workload evidence, not as a speedup claim.

Change `NodeIdentity.create` to distinguish `None` from invalid values. `None`
continues to generate a fresh ID; a `NodeId` is used unchanged; any other
runtime value raises a clear `ValueError`. Add tests proving `0` is rejected
and a valid explicit `NodeId` remains stable.

## Compatibility and Failure Behavior

The benchmark change is additive and retains historical case identifiers.
Invalid inputs that previously triggered random identity generation will now
fail explicitly; valid documented inputs (`None` or `NodeId`) retain behavior.
No changes are made to authorization policy, persisted schema, or cryptographic
behavior.

## Validation

- Regression test for `NodeIdentity.create(0)` fails before the implementation
  change and passes afterward.
- Benchmark tests assert the 200k workload is selected and emitted by the
  release harness.
- Run the 200k role-authorization benchmark and verify its fixture metadata and
  successful result.
- Run focused identity and benchmark tests, configured lint/format/type checks,
  the full unittest suite, and `git diff --check`.

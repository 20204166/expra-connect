# Large-Scale Role Authorization and Node Identity Design

## Goal

Extend cluster-state correctness evidence across 1,000, 30,000, 100,000, and
200,000 records, add separate role-authorization workload categories, and make
`NodeIdentity.create` reject invalid falsey node-ID arguments such as `0`
instead of silently generating a different identity.

## Existing Ownership

- `scripts/benchmark_cluster_state.py` owns staged cluster-state benchmark
  fixtures, role-authorization workloads, and scale correctness preflight.
- `scripts/benchmark_coverage.py` and `scripts/benchmark_release_wheels.py`
  own workload inventory and release-wheel benchmark orchestration.
- `NodeIdentity.create` in `src/expra_connect/identity/__init__.py` owns
  optional caller-supplied node-ID construction.
- `RoleState.allows` in `src/expra_connect/cluster/roles.py` remains unchanged
  unless separately authorized profiling demonstrates a production defect.

## Design

Exercise all four scales (1k, 30k, 100k, 200k) against an explicit cluster
correctness matrix: coordinator allow, worker denial, subcoordinator allow with
a live grant, denial without a grant, denial for an expired grant, paused/revoked
membership denial, invalid-clock denial, role/cluster serialization round trips,
and cluster hydration membership/epoch invariants. Add distinct timed auth cases
to the targeted supplement command and canonical inventory, preserving the
legacy coordinator-allow case ID. The existing `role authorization` selector
selects the full auth-case group. Store new measurements in append-only,
numbered supplement shards and merge non-overlapping shards in the diagram
updater; never rewrite existing result files. Use already-built paired wheels
when available, without rebuilding historical artifacts. Keep ordinary
all-operation matrix defaults unchanged. YAML round trips remain capped at the
existing supported size and are never counted as covered at larger scales.
Preserve result schema. Report timings as workload evidence, not as a speedup
claim, and describe coverage as cluster-subsystem coverage rather than
full-package coverage.

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
- Scale tests prove authorization, role/cluster serialization, and hydration
  invariants at 1k, 30k, 100k, and 200k. YAML coverage above its configured cap
  remains explicitly unsupported.
- Benchmark tests assert each authorization category is selected and emitted at
  all four sizes, and its correctness preflight succeeds before timings run.
- Append-only numbered supplement shards preserve existing files, pair native
  and universal measurements, and merge without duplicate cases in the updater.
- Run the new auth shard against existing paired 2.2.1.0 wheels without
  rebuilding; verify the latest version's measured coverage and leave older
  versions' added cases unmeasured.
- Run focused identity and benchmark tests, configured lint/format/type checks,
  the full unittest suite, and `git diff --check`.

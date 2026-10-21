# State Correctness and Runtime Performance Design

## Goal

Improve correctness across Expra Connect's stateful boundaries without a
meaningful performance regression, using existing performance comparisons as
context rather than as causal proof. Identify whether PyPy or selective Cython
is a suitable optimization path only after measuring the same workloads.

## Existing evidence and interpretation

- The performance diagrams in `docs/images/` are generated from saved
  `docs/performance-build-comparison-results/` records. The full comparison
  uses 1.8.0.0 as its historical anchor; the latest matrix compares
  2.2.3.0 builds against matching 2.2.1.0 flavors. These answer different
  questions and must not be conflated.
- In the latest matrix, 2.2.3.0 native scores 108.716p weighted and 107.899p
  full-workload versus 2.2.1.0 native; 2.2.3.0 universal scores 83.151p and
  82.344p versus 2.2.1.0 universal. Lower is faster. The native result is
  slower on the aggregate comparison; the universal result is faster. These
  aggregate figures do not identify a code-level cause.
- Existing per-case comparisons show trade-offs: JSON store saves improved
  versus the 2.2.1.1 comparisons at tested sizes, while some native
  cluster-state measurements were slower and universal cluster-state results
  were faster. The saved runs use different sampling configurations and are
  not a controlled before/after for a single source change. Use exact
  case/fixture/configuration matches and fresh paired runs for decisions.
- The historical chart summarizes the benchmark mix, not every deployment
  workload. It omits unmeasured cases from latency scores and reports coverage
  separately; no latency should be fabricated for an unavailable or unmatched
  case.
- Current correctness and performance guidance is in
  `docs/EDGE_CASE_RESEARCH.md`, `docs/DISCOVERY_EDGE_CASE_PARITY.md`,
  `docs/native-wheel-performance-baseline.md`, and
  `docs/performance-next-targets.md`. The scale tests already cover cluster
  role/cluster serialization and hydration at 1k, 30k, 100k, and 200k. Existing
  tests are distributed by state owner; this work will inventory gaps rather
  than assume a lack of tests means incorrect behavior.
- State responsibilities are distinct: `persistence.py` owns generic file
  codec and atomic-write behavior; cluster state has its own serializer and
  persistence adapter; pairing/trust, logical sessions, connection/retry
  state, profile lock/migration, and transport-generation continuity have
  separate canonical owners. The inventory must preserve those boundaries.
- Native wheels compile a selected set of modules using Cython and retain the
  Python sources; this is CPython-specific packaging. PyPy runs the process
  under a different interpreter and JITs hot code in that process. It does not
  selectively JIT one function inside a CPython process. A PyPy assessment is
  therefore a whole-runtime compatibility/performance experiment; a Cython
  assessment is a separate selective-module experiment.

## Scope

### Included state areas

1. Durable state and migrations: JSON, YAML, MessagePack, profile migration,
   transport-generation persistence, and cluster persistence.
2. Cluster role/authorization state: serialization, validation, hydration,
   fencing-related state, and persisted replacement behavior.
3. Pairing/trust and registry projections: directional trust semantics,
   revocation, rediscovery, and relationship to membership and connection.
4. Logical sessions: authentication, expiry, resume, generation retirement,
   and stale-result fencing.
5. Connection and retry state: status transitions, route failure classification,
   retry/backoff decisions, and lifecycle callbacks.
6. Profile lock and identity state: acquisition/release, corruption, restart,
   migration, rollback, and rotation-related persistence.

This is a bounded state-correctness review of current owners, not a rewrite of
all state APIs, a promise to prove every possible behavior correct, or a broad
network/platform validation campaign. Discovery and runtime lifecycle paths
are followed where they mutate or project these state areas.

### Performance scope

- Preserve behavior, serialized formats, validation limits, atomic durability,
  authorization, and generation fencing.
- Use same-host, same-runtime, same-fixture before/after measurements for
  changed paths. Report p50/p95, spread, fixture metadata, and module provenance.
- Treat “no meaningful regression” as no repeatable deterioration beyond
  measured run-to-run variation in affected and representative whole-suite
  workloads. A literal zero measured delta is not a reliable gate because
  timings vary. Any unresolved regression blocks keeping an optimization.
- Do not add runtime dispatch or a PyPy subprocess boundary solely to execute a
  small hot function; transfer/serialization costs and lifecycle complexity
  must be measured against a real caller workload.

## Design and execution phases

### Phase 1: Evidence inventory

- Read the complete performance comparison diagrams and their source records,
  and reconcile summary claims with the saved cases and comparison rules.
- Build an owner-by-owner state matrix: invariant, state transitions,
  persistence/load/write boundary, failure/recovery behavior, existing tests,
  uncovered cases, and benchmark coverage where meaningful.
- Use repository contracts and current source as authority. Record suspected
  gaps separately from confirmed failures. Do not change behavior in this
  phase.

### Phase 2: Correctness candidates

- Prioritize only concrete gaps supported by source/test evidence or a
  reproducible failure. Keep each fix scoped to its canonical owner.
- For each accepted candidate, write a focused regression test first, observe
  the expected failure, make the smallest behavior-preserving correction, and
  validate adjacent success, failure, boundary, and rollback cases.
- Preserve separation between trust, authorization, connection, logical
  session, membership, persistence, and identity-generation state. A state on
  one axis must never imply another without an explicit existing contract.
- If no correctness gap is confirmed for an owner, record that and leave its
  behavior unchanged.

### Phase 3: Runtime and selective acceleration evaluation

- Establish a reproducible pure-Python/source baseline on CPython and run
  relevant correctness tests under each candidate interpreter before timing.
- Test PyPy as a complete runtime using a compatible dependency set and the
  same representative package workloads. Record interpreter version, JIT
  warmup, modules/import provenance, workload shape, timing spread, and
  compatibility failures. Do not compare a warmed PyPy run to a cold CPython
  run or claim a selective-function JIT effect.
- Consider Cython only if profiling identifies a CPU-bound pure-Python loop or
  repeated Python-level work that remains material. Keep an experiment limited
  to the smallest justified module or kernel; preserve the source implementation
  and existing pure-Python wheel behavior. Compare equivalent CPython source and
  native-wheel workloads and verify extension provenance.
- Do not Cythonize wrappers around C-backed JSON, MessagePack, or filesystem
  work and assume their underlying work becomes compiled. Existing comparison
  notes show that compiling a wrapper does not compile those libraries.
- Keep PyPy and Cython candidates separate so benchmark differences remain
  attributable. If neither improves a representative end-to-end workload with
  behavior intact, recommend no acceleration change.

## Validation and acceptance

- Every changed invariant has a test that failed before the implementation and
  passes afterward, plus relevant neighboring state-owner tests.
- Existing test coverage and benchmark cases are not removed or weakened.
- Persistence, security, authorization, ordering, recovery, and lifecycle
  semantics remain intact; failures remain visible and state is not silently
  deleted or repaired.
- For each behavior change or retained optimization, matched repeated
  measurements show no meaningful regression in affected and representative
  whole-suite workloads. Report noise or inconclusive outcomes rather than
  claiming a win.
- PyPy support is not claimed unless package installation, dependencies, and
  relevant correctness tests work under the selected PyPy version. Cython
  benefits are not claimed without a source/native matched benchmark.
- Run the repository's full unittest, Ruff, Pyright, and Mypy checks before
  completion; run packaging checks for any wheel or interpreter support change.

## Non-goals

- No broad state-model redesign, schema/API change, or relaxation of validation
  and safety limits.
- No forcing strict zero-nanosecond performance change; normal timing variance
  makes that unverifiable.
- No port of all state machinery to Cython, no dual interpreter embedded in
  one process, and no PyPy deployment promise before compatibility evidence.
- No release wheel build or historical artifact replacement merely to complete
  the initial inventory.
- No changes justified only by aggregate chart colors or unlike benchmark
  configurations.

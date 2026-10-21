# State Correctness and Runtime Assessment Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Identify evidence-backed correctness gaps across all stateful Expra Connect areas and evaluate PyPy versus selective Cython without accepting a meaningful performance regression.

**Architecture:** Begin with a read-only state-owner and benchmark inventory. Run existing correctness checks and matched source benchmarks under CPython, then assess PyPy as a whole runtime if its installed dependencies allow it. Do not change behavior or add acceleration speculatively; confirmed correctness gaps and any justified optimization receive separate, narrow implementation plans.

**Tech Stack:** Python 3.12 / PyPy if available, `unittest`, project benchmark scripts, saved wheel comparison data, Cython release verifier.

---

## File map

- `docs/specs/2026-10-21-state-correctness-and-runtime-performance-design.md` — approved requirements and acceptance criteria.
- `docs/EDGE_CASE_RESEARCH.md`, `docs/DISCOVERY_EDGE_CASE_PARITY.md`, `docs/CLUSTER_TEST_PARITY.md`, `docs/ARCHITECTURE.md` — existing state invariants, edge-case dispositions, and ownership boundaries.
- `docs/native-wheel-performance-baseline.md`, `docs/performance-next-targets.md`, `docs/performance-build-comparison-results/`, `docs/images/performance-comparison.svg`, `docs/images/performance-latest-build-comparison.svg` — performance data, limitations, and historical/latest comparisons.
- `src/expra_connect/persistence.py`, `src/expra_connect/cluster/state.py`, `src/expra_connect/cluster/operations.py`, `src/expra_connect/cluster/persistence.py`, `src/expra_connect/pairing/manager.py`, `src/expra_connect/pairing/flow.py`, `src/expra_connect/remote/session.py`, `src/expra_connect/connections/state.py`, `src/expra_connect/connections/manager.py`, `src/expra_connect/identity/profile_state.py`, `src/expra_connect/identity/transport_generation.py`, and `src/expra_connect/nodes/registry.py` — canonical owners to inspect; do not edit during this assessment plan.
- `tests/test_persistence.py`, `tests/test_cluster.py`, `tests/test_cluster_persistence.py`, `tests/test_cluster_state_scale_correctness.py`, `tests/test_pairing.py`, `tests/test_pairing_flow.py`, `tests/test_session.py`, `tests/test_connection_state.py`, `tests/test_connection_candidates.py`, `tests/test_registry.py`, `tests/test_profile_state.py`, `tests/test_profile_migration.py`, `tests/test_identity_rotation.py`, `tests/test_peer_lifecycle.py`, and `tests/test_runtime.py` — current correctness evidence by owner.
- `scripts/benchmark_cluster_state.py`, `scripts/benchmark_persistence.py`, and `scripts/verify_wheel.py` — source workload timing and native-module provenance; no result files are to be overwritten in this phase.

## Task 1: Establish state-owner and test-coverage map

**Files:** Read-only inspection of the files listed above. No source or test edits.

- [ ] Confirm the working tree and branch with `git status --short --branch`; preserve any changes that appear during the assessment.
- [ ] Read repository instructions and the approved design spec before inspecting implementation.
- [ ] For each of the six spec areas—durable state/migration, cluster role/authorization, pairing/trust/registry, logical sessions, connection/retry/lifecycle, and profile/identity/transport rotation—record the canonical owner, public/cross-module callers, invariant, state transitions, durable versus process-local data, failure and recovery path, and directly proving tests.
- [ ] Trace every claimed gap in `docs/EDGE_CASE_RESEARCH.md` and `docs/DISCOVERY_EDGE_CASE_PARITY.md` to current code and its named test. Label each as covered, partially covered, unverified, or contradicted; do not treat documentation alone as runtime proof.
- [ ] Search for bypasses or duplicate ownership only along these state paths. Keep discovery, trust, authorization, connection, logical sessions, membership, and persistence as distinct axes.
- [ ] Report the owner/test matrix and evidence gaps in chat. Do not create a broad audit report or change behavior in this task.

## Task 2: Verify existing correctness baseline

**Files:** Existing tests only. Inspect test setup and helpers for persistent side effects before execution; do not assume a temporary test directory isolates any shared setup.

- [ ] Run the focused owner suites:

  ```sh
  .venv/bin/python -m unittest \
    tests.test_persistence tests.test_cluster tests.test_cluster_persistence \
    tests.test_cluster_state_scale_correctness tests.test_pairing \
    tests.test_pairing_flow tests.test_session tests.test_connection_state \
    tests.test_connection_candidates tests.test_registry \
    tests.test_profile_state tests.test_profile_migration \
    tests.test_identity_rotation tests.test_peer_lifecycle tests.test_runtime
  ```

- [ ] If a focused suite fails, capture the exact test and failure before drawing a conclusion. Do not “fix” failures during this evidence phase; establish whether they are baseline failures or the documented behavior is unsupported.
- [ ] If the focused suites pass, run `.venv/bin/python -m unittest discover -s tests -t .` once to detect neighboring integration failures. Expected outcome is exit code 0; any failure remains an open finding, not a pass.
- [ ] Record exactly which state areas the executed suite covered and which platform/network behaviors remain untested.

## Task 3: Reconcile saved performance diagrams and source records

**Files:** Read-only inspection of the performance documents, both SVG diagrams, and the raw source result files.

- [ ] Run `.venv/bin/python -m scripts.update_performance_diagrams --check`. Expected outcome: generated diagrams are current; if stale, report the stale state and do not regenerate them in this assessment.
- [ ] Verify the newest native/universal comparison uses the matching 2.2.1.0 flavors and that the historical 1.8.0.0 anchor is not presented as a controlled incremental comparison.
- [ ] Check each comparison candidate against operation, fixture shape, size, sample configuration, runtime, and module provenance in the raw JSON. Separate unsupported/unmeasured cases from measured slowdowns.
- [ ] Report the aggregate and per-suite trade-offs in chat, including why aggregate ratios cannot identify a correctness defect or isolate Cython's causal effect.

## Task 4: Capture matched CPython source performance baseline

**Files:** `scripts/benchmark_cluster_state.py` and `scripts/benchmark_persistence.py` are read-only harnesses; write any temporary logs only under `/tmp/opencode/`. Do not overwrite saved release JSON or rebuild wheels.

- [ ] Confirm the benchmark imports source modules by checking the `module=... path=...` lines and record Python/platform, fixture, warmups, repetitions, payload bytes, p50, p95, and range.
- [ ] Run the cluster baseline:

  ```sh
  .venv/bin/python -m scripts.benchmark_cluster_state \
    --sizes 1000 30000 100000 --warmups 2 --repetitions 5 \
    --skip-allocation-tracking
  ```

  Existing 100k YAML operations remain unavailable under the current 30k cap; do not change the cap for this assessment.

- [ ] Run the persistence baseline:

  ```sh
  .venv/bin/python -m scripts.benchmark_persistence \
    --sizes 30000 100000 200000 --warmups 2 --repetitions 9
  ```

  Preserve explicit unsupported 200k MessagePack outcomes and do not treat them as regressions.

- [ ] Compare against saved results only where workload definition, runtime, measurement configuration, and module flavor match. Mark unlike comparisons diagnostic, not causal.
- [ ] Profile the representative pure-Python cluster serializer paths, separately from timing comparisons:

  ```sh
  .venv/bin/python -m cProfile -s cumulative -m scripts.benchmark_cluster_state \
    --sizes 30000 --warmups 0 --repetitions 1 \
    --skip-allocation-tracking --operation 'role decode'
  .venv/bin/python -m cProfile -s cumulative -m scripts.benchmark_cluster_state \
    --sizes 30000 --warmups 0 --repetitions 1 \
    --skip-allocation-tracking --operation 'cluster round-trip'
  ```

  Treat profiler timing as instrumented diagnostic only. A Cython candidate requires a material Python-level hotspot in the profile, not merely a large wall-clock result in a codec or filesystem operation.

## Task 5: Evaluate PyPy runtime feasibility and parity

**Files:** No source edits or dependency installation. Use the installed `/usr/bin/pypy3` only.

- [ ] Record `/usr/bin/pypy3 --version` and check imports with `/usr/bin/pypy3 -c 'import msgpack, yaml, cryptography'`. If dependencies are missing, record PyPy as blocked in this environment; do not install packages without approval.
- [ ] If imports work, run the focused correctness suites from Task 2 under PyPy using the exact same test files. Record compatibility failures separately from behavior failures.
- [ ] Run the same cluster and persistence commands from Task 4 under PyPy only after the corresponding correctness suites pass. Use identical fixtures/repetition counts and allow JIT warmup; keep PyPy's whole-process results separate from CPython results.
- [ ] Do not propose running one hot function under a spawned PyPy subprocess unless a real product call path justifies the process and serialization overhead and an end-to-end experiment measures it.

## Task 6: Select bounded follow-up implementation work

**Files:** None in this task; findings and next proposals are delivered in chat.

- [ ] For every candidate correctness gap, cite current owner, exact evidence, affected invariant, and whether the failure is reproduced or only a test-coverage gap.
- [ ] Rank candidates by correctness impact and boundary risk; exclude speculative rewrites and changes unsupported by current behavior/tests.
- [ ] Recommend Cython only when a profile shows material CPU-bound Python work in an eligible module. Confirm whether the candidate is already in `scripts/verify_wheel.py`'s Cython module list; preserve Python sources and the universal wheel behavior.
- [ ] Recommend PyPy only if dependency compatibility, correctness parity, warmed matched workload timing, and distribution are acceptable for the whole process.
- [ ] Stop after presenting the evidence and proposed bounded follow-ups. Create a separate implementation plan for each confirmed issue or acceleration experiment after the user selects the next target; do not silently turn this assessment plan into a multi-owner patch.

## Execution constraints

- Before any later Python edit, load `senior-python-engineering` and select the general, testing, persistence/serialization, and performance scopes applicable to that edit.
- Use TDD for each future behavior fix: write a focused failing regression first, observe the intended failure, then make the smallest change and run adjacent owner tests.
- Do not weaken validation, security, atomic durability, migration, replay/session, identity-rotation, or authorization semantics for speed.
- No code, test, dependency, wheel, or generated-result changes are authorized by this assessment plan.
- Full-state correctness cannot be proven exhaustively; report bounded evidence and unverified platform/network cases honestly.

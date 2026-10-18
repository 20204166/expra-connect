# Cluster-State Round-Trip Performance Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Simplify and improve cluster-state and role-state round-trip latency or allocation without changing persisted behavior.

**Architecture:** Keep strict role-state encoding/decoding in `cluster/state.py` and aggregate schema-v2 compatibility in `cluster/operations.py`. Measure encode, decode, and end-to-end behavior on the current checkout before changing implementation; only keep a local change when same-source repeated measurements demonstrate value.

**Tech Stack:** Python 3.10+, unittest, `time.perf_counter`, `tracemalloc`, Ruff, Pyright, Mypy.

---

## File map

- `src/expra_connect/cluster/state.py`: canonical strict role-state codec; modify only if benchmark/profile implicates it.
- `src/expra_connect/cluster/operations.py`: schema-v2 `ClusterState` codec and compatibility; modify only if benchmark/profile implicates it.
- `src/expra_connect/cluster/models.py`: contains the recently optimized `RoleAssignment.__post_init__`; touch only if profiling identifies a remaining material cost.
- `tests/test_role_engine.py`: role-state serialization shape and runtime-only job normalization.
- `tests/test_cluster_persistence.py`: aggregate round-trip, schema compatibility, invites, and malformed inputs.
- `tests/test_cluster.py`, `tests/test_role_hardening.py`: requested behavioral regression suites.
- `scripts/benchmark_cluster_state.py`: repeatable in-memory benchmark for 1k, 30k, and 100k states, splitting dict codec and YAML serializer/round-trip costs with payload size and peak traced allocation.

## Task 1: Pin codec behavior before implementation

**Files:**
- Modify: `tests/test_role_engine.py`
- Modify: `tests/test_cluster_persistence.py`

- [ ] **Step 1: Add exact role-state round-trip coverage.**

Add a test constructing role assignments, epoch, promotion epochs, and capability grants, then assert the exact result of `role_state_to_dict`, round-trip equality for persisted fields, and `has_active_job=False` after decoding even when it was true before encoding.

- [ ] **Step 2: Run the focused role test and confirm the added test passes on current behavior.**

Run: `.venv/bin/python -m unittest tests.test_role_engine`
Expected: `OK`. These are characterization tests and should document existing behavior, not introduce a behavior change.

- [ ] **Step 3: Add complete aggregate compatibility coverage.**

Extend the persistence tests to round-trip a `ClusterState` containing multiple assignments, epoch, promotion epochs, capability grants, active invites, join admissions, and used-invite hashes. Assert the emitted schema version, legacy `role` field, `local_id` alias, and absence of raw invite token material. Preserve the existing legacy alias and used-token migration tests rather than replacing them.

- [ ] **Step 4: Run the focused persistence tests.**

Run: `.venv/bin/python -m unittest tests.test_cluster_persistence`
Expected: `OK`.

## Task 2: Establish current-source performance and allocation baseline

**Files:**
- Create: `scripts/benchmark_cluster_state.py`

- [ ] **Step 1: Add benchmark fixture and CLI skeleton.**

Create a script with deterministic fixtures at 1,000, 30,000, and 100,000 bound worker assignments and matching capability grants. Keep fixture composition constant at each size. Add command-line options for repetitions, warmups, and the maximum size for YAML measurements; use 1 warmup and 3 timed samples by default because PyYAML serialization is substantially more expensive. Measure YAML through 30,000 entries and dict codecs at all three sizes. Do not add benchmark dependencies.

- [ ] **Step 2: Implement distinct encode, decode, and round-trip measurements.**

Measure `role_state_to_dict` for encode; decode a prebuilt mapping with `role_state_from_dict`; measure aggregate `ClusterState.to_dict` and `ClusterState.from_dict` separately; and report end-to-end aggregate round-trip. Also measure YAML `safe_dump`, `safe_load`, and the combined in-memory YAML-to-`ClusterState` path using the exact options in `YamlStateStore`. Use `time.perf_counter` and report median and min/max sample time in microseconds. Keep filesystem writes, `fsync`, and atomic replacement out of this serializer-only benchmark.

- [ ] **Step 3: Report payload size and peak allocation.**

Measure latency and allocation in separate passes so tracing overhead does not contaminate timing. For allocation, start `tracemalloc`, take a baseline snapshot, reset the peak, run one operation, read peak traced bytes, and stop tracing. Report the encoded mapping's UTF-8 JSON byte count as a stable size indicator. Do not include fixture construction in operation timing or allocation samples.

- [ ] **Step 4: Run the baseline on the current checkout.**

Run: `.venv/bin/python -m scripts.benchmark_cluster_state`
Expected: output for each configured size and each applicable operation, including sample median/range, peak traced bytes, and JSON/YAML payload bytes. YAML operations run through 30,000 entries; dict operations include 100,000. Record Python version and platform. Do not compare these measurements directly to historical wheel data because source versions and benchmark dimensions differ.

## Task 3: Profile and make one evidence-backed local change

**Files:**
- Modify only the implicated owner among `src/expra_connect/cluster/state.py`, `src/expra_connect/cluster/operations.py`, and, if demonstrated, `src/expra_connect/cluster/models.py`.

- [ ] **Step 1: Profile the slow operation at 30k and 100k.**

Run the benchmark workload under `cProfile` for the implicated operation and inspect cumulative time. In the script, profile the operation callable with representative preconstructed input; keep profiler overhead out of reported benchmark numbers.

- [ ] **Step 2: Select one measured hotspot and state the expected work reduction.**

Identify the measured function/call count responsible for a significant share of time or allocation. Consider only reducing repeated conversions, copies, or traversals that the profile confirms. Do not add caching or a generic codec abstraction without evidence.

- [ ] **Step 3: Add a regression test first if the change affects behavior or an ambiguous edge.**

Place the test in the existing owner-specific test file. Cover serialized data and externally observable decoded state; include malformed/legacy cases only when the modified branch touches them.

- [ ] **Step 4: Make the smallest change in the canonical owner.**

Preserve strict input validation, exception types, field ordering, schema-v2 defaults, aliases, invite validation and hashing, persisted token secrecy, and runtime-only job normalization. Do not change unrelated cluster logic.

- [ ] **Step 5: Run focused tests immediately.**

Run: `.venv/bin/python -m unittest tests.test_cluster tests.test_cluster_persistence tests.test_role_engine tests.test_role_hardening`
Expected: `OK`.

- [ ] **Step 6: Re-run identical benchmark and compare.**

Run: `.venv/bin/python -m scripts.benchmark_cluster_state`
Expected: same workload and environment as baseline. Retain the change only if repeated results show a useful latency/allocation improvement and no material regression at another measured size; otherwise simplify/revert only the agent-authored optimization.

## Task 4: Full verification and diff review

**Files:**
- Review changes to the files listed above; do not alter unrelated working-tree files.

- [ ] **Step 1: Run the full unittest suite.**

Run: `.venv/bin/python -m unittest discover -s tests -t .`
Expected: all discovered tests pass.

- [ ] **Step 2: Run formatting and lint checks.**

Run: `ruff check .`
Run: `ruff format --check .`
Expected: both commands exit successfully.

- [ ] **Step 3: Run static type checks.**

Run: `pyright`
Run: `.venv/bin/mypy --ignore-missing-imports src tests`
Expected: both commands exit successfully.

- [ ] **Step 4: Review the final diff and working tree.**

Run: `git diff --check` and `git status --short`. Confirm only the intended codec, test, benchmark, and plan/spec files changed; leave pre-existing `docs/performance-next-targets.md` untouched. Do not commit or push unless separately requested.

## Plan self-review

- Spec coverage: ownership and scope, current `bee000a` baseline, split dict/YAML timing and allocation measures, compatibility requirements, behavior tests, and full repository checks each have explicit tasks.
- No placeholder steps remain; any implementation change is contingent on profile evidence.
- The benchmark is part of the deliverable so later changes can reproduce comparisons; measurements use the same source and fixture on both sides.

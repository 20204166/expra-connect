# Native-wheel Workload Coverage Expansion Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add 15 deterministic benchmark cases and integrate immutable native/universal supplements for 2.2.1.0, bringing the distinct workload inventory from 74 to 89 without changing historical measurements.

**Architecture:** Extend the existing connect, cluster-state, and persistence benchmark owners; use the existing benchmark result schema and wheel-import isolation. Add narrowly scoped supplement discovery/validation/merge to `scripts/update_performance_diagrams.py`, preserving originals and archive behavior. Regenerate the baseline and SVGs from validated raw data.

**Tech Stack:** Python 3.10+, unittest, existing benchmark support, JSON result files, wheel artifacts, Markdown/SVG updater.

---

## File map

- `tools/connect_performance_benchmark.py`: own three discovery, three pairing/trust, and three capability/surface deterministic cases.
- `scripts/benchmark_cluster_state.py`: own three `RoleState.allows` scale cases.
- `scripts/benchmark_persistence.py`: canonical owner of the already-existing three end-to-end profile migration file-count cases; wire them into release-suite results and supplements without duplicating the operation or its fixture logic.
- `scripts/benchmark_release_wheels.py`: run added operations from extracted wheel contents and emit standalone supplement JSON without replacing regular suite results.
- `scripts/update_performance_diagrams.py`: validate, merge in memory, and archive supplements alongside original version/flavor results.
- `tests/test_connect_performance_benchmark.py`, `tests/test_release_performance_benchmarks.py`, `tests/test_performance_diagrams.py`, `tests/test_persistence_benchmark.py`, and new `tests/test_cluster_state_performance_benchmark.py`: behavior, provenance, immutability, and coverage assertions.
- `docs/performance-build-comparison-results/`: add six 2.2.1.0 supplement JSON files (three suites × two flavors); originals remain byte-for-byte unchanged.
- `docs/native-wheel-performance-baseline.md`, `docs/images/performance-comparison.svg`, `docs/images/performance-latest-build-comparison.svg`: generated/updated presentation.

## Task 1: Add deterministic connect feature cases (9 cases)

**Files:** Modify `tools/connect_performance_benchmark.py`; add/extend tests in `tests/test_connect_performance_benchmark.py`.

- [x] **Step 1: Characterize cases first.** Added assertions for the exact nine stable case IDs, measured status, and repetition metadata.
- [x] **Step 2: Run focused tests and confirm failure.** The new test first failed because all nine case IDs were absent.
- [x] **Step 3: Implement the nine operations in the existing operation-spec path.** Implemented discovery validation/ranking/TTL expiry, pairing relationship and transport classification, capability dispatch, and peer/cluster surface dispatch through canonical package APIs.
- [x] **Step 4: Verify focused tests and CLI selection.** The complete connect benchmark test module passes (6 tests); the new suite run at zero warmups/one repetition measures all nine cases.

## Task 2: Add cluster-policy cases and integrate existing migration cases (6 cases total)

**Files:** Modify `scripts/benchmark_cluster_state.py` and `scripts/benchmark_release_wheels.py`; add `tests/test_cluster_state_performance_benchmark.py` and extend `tests/test_release_performance_benchmarks.py` and `tests/test_persistence_benchmark.py`.

- [x] **Step 1: Reconfirm existing migration behavior.** Reused existing migration fixtures and CLI; new coverage test confirms the 1/16/64 IDs and fixture counts.
- [x] **Step 2: Add failing cluster-policy tests.** Added the 1k/30k/100k role-authorization test.
- [x] **Step 3: Verify RED.** Cluster-policy test failed on the absent `role authorization` operation; the persistence and migration tests passed.
- [x] **Step 4: Implement the three cluster-policy operations.** Added `RoleState.allows` over the existing coordinator/worker fixtures.
- [x] **Step 5: Wire migration outcomes into full future release runs.** The wheel runner now executes existing migration mode and merges its outcomes only when writing newly produced persistence results.
- [x] **Step 6: Verify GREEN.** All 22 focused connect, cluster, persistence, and release-runner tests pass; existing complete historical files remain reused without overwrite.

## Task 3: Produce append-only wheel supplements

**Files:** Modify `scripts/benchmark_release_wheels.py`; add tests in `tests/test_release_performance_benchmarks.py`.

- [x] **Step 1: Test supplement naming, immutability, and provenance.** Tests cover paired artifacts, separate supplements, immutable originals, rerun reuse, and refusal of partial sets.
- [x] **Step 2: Verify failure.** The `--feature-workloads-only` CLI test first failed because the option was not recognized.
- [x] **Step 3: Add a dedicated supplement command path.** Added `--supplements-only` and optional `--version`; the runner executes the added cases from each wheel, validates package/module provenance and exact case inventory, and publishes separate result files atomically without overwrite.
- [x] **Step 4: Verify with synthetic wheels/results.** Focused release tests pass, including intact original bytes and paired native/universal reuse; actual artifacts were run and generated six supplements.

## Task 4: Validate and merge supplements in the updater

**Files:** Modify `scripts/update_performance_diagrams.py`; add tests in `tests/test_performance_diagrams.py`.

- [x] **Step 1: Add fixture-based tests** for valid merge, duplicates/conflicts, incomplete pairs, metadata/module flavor mismatch, fixture parity, original-byte preservation, and archive association.
- [x] **Step 2: Verify tests fail** with a regression test first: before loader support, the valid-merge test failed because cluster policy cases were missing from the loaded inventory.
- [x] **Step 3: Implement supplemental loading.** The updater requires paired complete suites, exact case inventory, matching runtime/config/fixtures and correct flavor provenance. It merges in memory; archived build JSON globs include supplements alongside original results.
- [x] **Step 4: Verify updater behavior.** Focused tests pass; actual history reports 89 cases, 84/89 for each 2.2.1.0 flavor, and three measured profile-migration cases. Existing result bytes remain unchanged.

## Task 5: Measure 2.2.1.0 artifacts and publish baseline

**Files:** Add six raw supplement JSON files under `docs/performance-build-comparison-results/`; update `docs/native-wheel-performance-baseline.md` and generated SVGs.

- [x] **Step 1: Capture original checksums and wheel provenance.** Verified wheel hashes match the baseline and original 2.2.1.0 JSON is unchanged in the worktree.
- [x] **Step 2: Run supplements from the existing artifacts.** Ran both existing wheels on CPython 3.12.3/Linux x86_64; migration cases used the existing benchmark mode. No wheels were rebuilt and no original JSON was overwritten.
- [x] **Step 3: Validate the complete pair and exact inventory.** Updater and runner validate all 15 unique cases, flavor parity, fixture/config/runtime parity, and module provenance; actual union is 89 and each flavor measures 84.
- [x] **Step 4: Refresh charts and summary.** Refreshed SVGs and the baseline; scores remain based only on matched positive-p50 cases, with no performance-improvement claim from coverage growth.
- [x] **Step 5: Verify generated output.** Updater `--check` passes and both generated SVGs parse as XML.

## Task 6: Full verification and review

- [x] **Step 1: Run focused suites** for all touched benchmark and updater code.
- [x] **Step 2: Run repository validation** per `AGENTS.md`: unittest discovery passed (955 tests, 26 skipped); `ruff check .` and `ruff format --check .` passed; `pyright --venvpath /home/btn17/Downloads/expra-connect` passed (0 errors); and Mypy passed with `PYTHONPATH=src:.`.
- [x] **Step 3: Review diff and integrity.** `git diff --check` passed; the two wheel SHA-256 values match the baseline and all six original 2.2.1.0 suite JSON files are unchanged.
- [x] **Step 4: Report exact outcomes.** No unrelated role/test edits are in this worktree; note the 26 existing skipped tests due absent optional benchmark source artifacts.

## Constraints

- Before the first Python edit, load `senior-python-engineering` and apply general Python, performance, security, persistence, and testing scopes relevant to the changed benchmark paths.
- Test-first: each new workload and supplement validation behavior gets a failing regression test before implementation.
- Reuse the canonical feature APIs and existing benchmark/result mechanisms; no new generic benchmark framework.
- Never overwrite published historical benchmark JSON or rebuild the 2.2.1.0 wheels.
- Do not edit `src/expra_connect/cluster/roles.py` or `tests/test_role_hardening.py`; those changes are outside this plan.

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

- [ ] **Step 1: Test supplement naming, immutability, and provenance.** Cover an existing complete result set plus missing supplements, already-complete supplements, partial supplements, and refusal to overwrite any existing target. Snapshot existing file bytes and assert unchanged.
- [ ] **Step 2: Verify failure.** Run the focused release benchmark tests and confirm supplement flow is not implemented.
- [ ] **Step 3: Add a dedicated supplement command path.** For each wheel, extract the package as the normal runner does, run the nine connect cases, three role-policy cases, and the existing three profile-migration cases under that wheel's code, validate package version and module paths, and write separate `<version>-<flavor>-<suite>-supplement.json` outputs using atomic/no-overwrite publication. Persist the same metadata/config across flavors. Keep normal release benchmark paths and schema compatible.
- [ ] **Step 4: Verify with synthetic wheels/results.** Run focused tests; malformed provenance, missing peer suites, partial supplements, and conflicts fail closed while regular result bytes stay identical.

## Task 4: Validate and merge supplements in the updater

**Files:** Modify `scripts/update_performance_diagrams.py`; add tests in `tests/test_performance_diagrams.py`.

- [ ] **Step 1: Add fixture-based tests** for valid in-memory merge; duplicate and conflicting IDs; wrong suite/version/flavor/runtime/config; imported module outside the recorded wheel flavor; missing native/universal pair; operation/fixture parity; unchanged original result bytes; and supplement archive association.
- [ ] **Step 2: Verify tests fail** with `.venv/bin/python -m unittest tests.test_performance_diagrams -v`.
- [ ] **Step 3: Implement supplemental loading.** Require exactly paired native/universal supplements for a version; validate against regular metadata and each other; reject duplicate IDs rather than choosing either. Merge outcomes only in memory. When normal history eviction archives a run, move its supplement to the same `history/` location policy; never delete or rewrite raw JSON.
- [ ] **Step 4: Verify updater behavior.** Run focused tests and confirm workload coverage sees each new case once, latency scoring remains matched-positive-p50 only, and originals are unchanged.

## Task 5: Measure 2.2.1.0 artifacts and publish baseline

**Files:** Add six raw supplement JSON files under `docs/performance-build-comparison-results/`; update `docs/native-wheel-performance-baseline.md` and generated SVGs.

- [ ] **Step 1: Capture original checksums and wheel provenance.** Record hashes for the six existing 2.2.1.0 suite JSON files and both wheel artifacts before running anything.
- [ ] **Step 2: Run supplements from the existing artifacts.** Use the matching CPython 3.12.3 interpreter/platform and identical warmups, repetitions, and allocation settings for both flavors. Use the existing profile-migration benchmark mode for its three cases; do not recreate its fixture or migration callbacks. Do not rebuild wheels or overwrite existing benchmark files. Retain raw samples and module provenance.
- [ ] **Step 3: Validate the complete pair and exact inventory.** Require 15 new unique cases and 89 total distinct workload IDs. Verify both flavors' case IDs, operation labels, fixtures, config, interpreter, and platform agree; existing suite files still match their pre-run hashes.
- [ ] **Step 4: Refresh charts and summary.** Run `.venv/bin/python -m scripts.update_performance_diagrams`; update baseline coverage counts and add feature-slice/provenance notes. Do not state that broader case coverage is a speedup. Preserve the existing matched-workload score rules.
- [ ] **Step 5: Verify generated output.** Run updater `--check`; parse generated SVGs with `xml.etree.ElementTree`; inspect updated summary counts and coverage denominator for every displayed build.

## Task 6: Full verification and review

- [ ] **Step 1: Run focused suites** for all touched benchmark and updater code.
- [ ] **Step 2: Run repository validation** per `AGENTS.md` using the available venv at `/home/btn17/Downloads/expra-connect/.venv` and `PYTHONPATH=src:.` for Python commands: unittest discovery, `ruff check .`, `ruff format --check .`, `pyright`, and mypy. Ruff is not installed in the available venv; report unavailable checks rather than changing repository dependencies.
- [ ] **Step 3: Review diff and integrity.** Run `git diff --check`; verify existing wheel and result hashes; inspect changed files for accidental unrelated edits, unstable benchmark setup, unsupported claims, or secret-bearing output.
- [ ] **Step 4: Report exact outcomes.** State any checks unavailable in this worktree rather than claiming they passed; keep the unrelated roles/test changes untouched.

## Constraints

- Before the first Python edit, load `senior-python-engineering` and apply general Python, performance, security, persistence, and testing scopes relevant to the changed benchmark paths.
- Test-first: each new workload and supplement validation behavior gets a failing regression test before implementation.
- Reuse the canonical feature APIs and existing benchmark/result mechanisms; no new generic benchmark framework.
- Never overwrite published historical benchmark JSON or rebuild the 2.2.1.0 wheels.
- Do not edit `src/expra_connect/cluster/roles.py` or `tests/test_role_hardening.py`; those changes are outside this plan.

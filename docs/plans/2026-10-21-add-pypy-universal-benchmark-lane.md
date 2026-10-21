# Add PyPy + Universal Benchmark Lane Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a separate PyPy-on-universal-wheel measurement lane for four selected discovery/pairing operations while retaining existing CPython native and universal release benchmarks.

**Architecture:** Continue building exactly the CPython-specific Cython native wheel and pure-Python universal wheel. Install the universal wheel into an isolated PyPy environment, benchmark only the four selected operations, and store/report PyPy results separately so runtime differences never enter CPython aggregate diagram scores. Do not optimize production code until the new results are reviewed.

**Tech Stack:** Python 3.11 CPython/PyPy, unittest, benchmark support scripts, release wheel wrapper, Cython native wheel, pure-Python universal wheel.

---

## File map

- `docs/specs/2026-10-21-pypy-universal-benchmark-design.md` — approved design and acceptance criteria.
- `scripts/build_release.sh` — accept an optional PyPy interpreter and invoke its lane before refreshing documentation/artifact synchronization.
- `scripts/benchmark_release_wheels.py` or a focused adjacent script — isolate/install the just-built universal wheel under PyPy, run the four feature operations, validate source/module provenance, and persist PyPy measurements separately.
- `scripts/benchmark_support.py` — make allocation tracking explicitly unavailable when PyPy lacks `tracemalloc`, while normal timing continues and requesting allocation tracking fails clearly.
- `tests/test_release_performance_benchmarks.py`, `tests/test_benchmark_support.py`, and/or focused new benchmark tests — prove lane selection, output naming, no overwrite, provenance, and unavailable allocation-tracking behavior.
- `docs/native-wheel-performance-baseline.md` — document the three configurations and show PyPy results in a separate section, not in CPython indices.
- `docs/performance-build-comparison-results/` — append-only separate PyPy result record(s); do not replace historical CPython records.
- `dist/` — release-wrapper-produced CPython native and universal wheels plus append-only checksums for the selected version.

## Task 1: Prepare gates and preserve baseline

- [ ] Verify `git status --short --branch`, the approved design, and current branch; preserve all existing commits and user changes.
- [ ] Record existing matched CPython 3.11 and PyPy 3.11 results for the four operations from the current session as pre-change context. Do not edit or overwrite saved files.
- [ ] Confirm the installed PyPy runtime can import the released dependencies and the selected source modules. Keep `LD_LIBRARY_PATH` scoped to the isolated runtime process.
- [ ] Before any Python file edit, load `senior-python-engineering`; select general, testing, performance, and subprocess/persistence scopes that match the change. Load `consolidating-responsibilities` before code edits and use TDD for behavior changes in the benchmark lane.

## Task 2: Add isolated PyPy/universal benchmark execution

**Files:** `scripts/build_release.sh`, `scripts/benchmark_release_wheels.py` (or one focused new benchmark script), `scripts/benchmark_support.py`, corresponding benchmark tests.

- [ ] Add a test first for a PyPy/universal-only lane that selects the four names exactly: `discovery candidate validation`, `discovery preferred endpoint`, `discovery registry add + expire`, and `pairing relationship projection`.
- [ ] Run the test and verify it fails because the PyPy lane is absent.
- [ ] Implement the smallest isolated workflow: build remains native + universal under the configured CPython; create a temporary PyPy environment, install the exact universal wheel, run the four existing harness operations with allocation tracking off, and capture exact interpreter, package, fixture/config, source-module path, and wheel identity.
- [ ] Ensure PyPy extension/runtime library paths are set only for PyPy subprocesses. Do not load CPython native extensions under PyPy.
- [ ] Keep PyPy result filenames and storage distinct from CPython native/universal records; fail closed on partial result sets or attempted overwrite of an existing release measurement.
- [ ] Handle PyPy's missing `tracemalloc` explicitly: timing works with allocation tracking disabled; allocation tracking requested on an unsupported runtime reports a clear unsupported error, not fabricated zero allocations.
- [ ] Run focused benchmark tests and the four operations under the isolated PyPy environment. Confirm module provenance resolves inside the installed universal wheel, not the checkout.

## Task 3: Integrate optional release workflow

**Files:** `scripts/build_release.sh`, tests for its argument/dispatch behavior.

- [ ] Add an optional explicit PyPy interpreter argument/environment setting; preserve existing behavior when omitted.
- [ ] When configured, run the PyPy/universal lane against the just-built universal wheel before chart refresh and checksum/artifact synchronization. The ordinary CPython release matrix still measures native and universal.
- [ ] Verify a PyPy lane failure prevents publishing/synchronizing partial artifacts, following existing release-wrapper failure ordering.
- [ ] Test wrapper argument validation without running a release build; confirm no arbitrary shell command execution and no changes to `tools/` library behavior.

## Task 4: Run release build and refresh performance documentation

- [ ] Determine the next release bump using `scripts/build_release.sh`; do not hand-edit `_version.py` or build wheels outside the wrapper.
- [ ] Build native and universal wheels and run their standard CPython benchmark matrix plus the new PyPy/universal selected-workload matrix. Keep complete output and raw results.
- [ ] Verify the wheel contents with the wrapper's `scripts/verify_wheel.py` checks: native remains CPython-tagged with expected Cython extensions and Python sources; universal remains `py3-none-any` without extensions.
- [ ] Refresh diagrams and summary through the wrapper. Add a separately labeled PyPy section to `docs/native-wheel-performance-baseline.md` with runtime/wheel provenance, repetitions, p50/p95/p99, and explicit non-comparability with CPython aggregate scores.
- [ ] Read the complete refreshed Markdown and raw PyPy records. Identify which of the four operations need attention; do not make production-code optimization changes in this task.

## Task 5: Verify, commit, and push

- [ ] Run focused benchmark tests, the full unittest suite, Ruff check/format, Pyright, and Mypy as required by `AGENTS.md`.
- [ ] Run performance diagram freshness check and `git diff --check`; verify no saved results were overwritten and every wheel for the built version has a matching `dist/SHA256SUMS` row.
- [ ] Review staged diff for only the benchmark lane, docs, results, and release artifacts; ensure no Cython list changes or production behavior changes slipped in.
- [ ] Commit all release wheels and checksums together with their source/benchmark/docs changes, then push `main` to `origin` as requested.
- [ ] Confirm push success and clean working tree; report benchmark findings and PyPy areas needing discussion.

## Constraints

- No PyPy runtime is embedded in the CPython native wheel. The PyPy lane uses the same pure-Python universal wheel.
- Do not mix PyPy timings into native/universal CPython diagrams or aggregate winner scores.
- Do not optimize production code before the new lane's results are reviewed; the current tail-latency evidence has not identified a safe code-level cause.
- Use only `scripts/build_release.sh` for wheel builds. Preserve historical release artifacts and append checksum rows.
- No system package or repository dependency changes are authorized by this plan.

# Latest-build pairwise performance diagram Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Generate a separate unweighted case-level diagram comparing the newest two complete paired native/universal benchmark versions.

**Architecture:** Extend `scripts/update_performance_diagrams.py`, the existing owner of benchmark result loading, SVG rendering, and freshness synchronization. Select the newest two versions with complete native/universal results for all three suites, render their four directional per-case comparisons, and add the new SVG to the normal generator output without changing weighted scoring.

**Tech Stack:** Python 3.12, unittest, existing benchmark result dataclasses, deterministic hand-built SVG.

---

## File map

- `scripts/update_performance_diagrams.py` — selection of paired versions, pairwise case rows, SVG rendering, and normal `--check` integration.
- `tests/test_performance_diagrams.py` — fixtures and regression coverage for selection, ratios, missing/unsupported cases, configuration warnings, and freshness.
- `docs/native-wheel-performance-baseline.md` — embed and explain the unweighted chart without changing the weighted winner section.
- `docs/images/performance-latest-build-comparison.svg` — generated output, written only by the generator.

## Task 1: Test selection and four comparison directions

**Files:** `tests/test_performance_diagrams.py`

- [x] Add deterministic synthetic `BenchmarkRun` data for two complete paired versions (`2.2.0.4`, `2.2.0.5`) across `cluster_state`, `persistence`, and `connect`.
- [x] Add `test_latest_pairwise_diagram_compares_newest_complete_versions` using two complete synthetic pairs (`2.2.0.4`, `2.2.0.5`) plus a later incomplete version. Assert the four comparison labels and that latest native `80` vs previous native `100` renders `0.800×`.
- [x] Add `test_latest_pairwise_diagram_handles_unsupported_and_missing_cases` to assert unsupported shared cases are labelled unsupported and IDs absent from either side get no fabricated ratio.
- [x] Add `test_latest_pairwise_diagram_flags_configuration_differences` to prove warmup/repetition differences across versions are called out.
- [x] Run `.venv/bin/python -m unittest tests.test_performance_diagrams.BuildComparisonTests.test_latest_pairwise_diagram_compares_newest_complete_versions` and confirm the expected failure is the missing renderer/selection behavior.

## Task 2: Implement the comparison data selection and SVG

**Files:** `scripts/update_performance_diagrams.py`

- [x] Implement `render_latest_build_pairwise_diagram(history: dict[str, tuple[BenchmarkRun, ...]]) -> str`, selecting the two newest versions having both flavors for every `BUILD_COMPARISON_SUITES` suite. Raise a clear `ValueError` if two such versions are unavailable.
- [x] Build rows only from identical `case_id` values on the two sides of each comparison. Preserve suite, size, operation, p50, and unsupported state; never synthesize a penalty or latency.
- [x] Render one standalone SVG with native and universal directional panels, each showing its same-flavor prior-version comparison and same-version cross-flavor comparison. Use candidate/reference ratios (`<1×` faster, `>1×` slower), identify units as p50 microseconds, and visually separate suites.
- [x] Add a visible note that the view is unweighted and excluded from the historical weighted score. Flag cross-version configuration differences using the compared runs' metadata; the current 2.2.0.4/2.2.0.5 cluster configuration difference must be visible.
- [x] Extend `render_diagrams`/the generator composition so `main()` loads the build history once, adds the new artifact, and `sync_diagrams` includes it in ordinary refreshes and `--check`. Do not touch weighted index selection or summary calculation.
- [x] Run the focused test from Task 1 and the full `tests.test_performance_diagrams` module; both must pass.

## Task 3: Document and refresh

**Files:** `docs/native-wheel-performance-baseline.md`, generated SVG

- [x] Add a concise bullet under the diagram section stating that the newest-build pairwise chart compares per-case p50s, is unweighted, and does not affect the weighted winner.
- [x] Embed `images/performance-latest-build-comparison.svg` adjacent to the existing comparison chart.
- [x] Run `.venv/bin/python -m scripts.update_performance_diagrams` and inspect the generated SVG's labels, case count, and caveat text.
- [x] Run `.venv/bin/python -m scripts.update_performance_diagrams --check`; expected output is `Performance diagrams and summary are current.`

## Task 4: Validate and publish

- [x] Run `.venv/bin/python -m unittest discover -s tests -t .` (922 tests passed, 26 skipped).
- [x] Run `ruff check .`, `ruff format --check .`, `pyright`, and `.venv/bin/mypy --ignore-missing-imports src tests` (all passed).
- [x] Run `git diff --check`; review that only the planned script, tests, documentation, generated SVG, and this plan changed, and that weighted-score output is unchanged.
- [x] Commit the completed feature, fast-forward `main`, push `origin main`, and verify the remote ref and clean working tree.

## Required implementation workflow

- Load `senior-python-engineering` before any Python edit; use the general and testing scopes.
- Use TDD: add each focused regression test first, run it and observe the expected failure, then implement the minimal behavior and rerun it.
- Preserve the established loader and renderer ownership in `scripts/update_performance_diagrams.py`; do not add a second benchmark parser, scoring path, or generic visualization framework.

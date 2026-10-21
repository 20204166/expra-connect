# PyPy + Universal Wheel Benchmark Design

## Goal

Add a reproducible PyPy benchmark lane for the four selected discovery and
pairing operations, alongside the existing CPython native and universal wheel
benchmarks, so runtime-specific follow-up is based on fresh evidence.

## Architecture

The release continues to build two artifacts: a CPython-specific native wheel
with the selected Cython extensions, and a pure-Python universal wheel. The
universal wheel is installed and run under PyPy as a separate interpreter
configuration; PyPy is not embedded in either wheel and does not share a native
wheel with CPython. PyPy measurements are reported separately from the existing
CPython release comparison scores because they use a different runtime and
benchmark lane.

The PyPy lane measures these four existing operations: discovery candidate
validation, discovery preferred endpoint selection, discovery registry add and
expire, and pairing relationship projection. It records interpreter and
package versions, wheel/module provenance, fixture/configuration, and p50/p95/
p99. Existing saved PyPy baseline results are retained for comparison. No
production behavior or performance code is changed before the fresh benchmark
results are reviewed.

## Build and benchmark flow

1. Extend the benchmark workflow with an optional/explicit PyPy interpreter
   configuration that consumes the just-built universal wheel and only runs
   the four selected operations. Native CPython benchmarks remain on CPython.
2. Build native and universal wheels through `scripts/build_release.sh`; retain
   its version selection, wheel verification, append-only checksum, benchmark,
   and chart-refresh behavior. Do not manually edit `_version.py`, wheel
   contents, or historical artifacts.
3. Run the standard CPython wheel matrix plus the separate PyPy/universal lane.
   Store PyPy raw results separately from the CPython versioned comparison
   records; do not mix runtimes in aggregate diagram scores.
4. Refresh the relevant performance Markdown with a clearly labeled PyPy
   table, runtime details, the four workload results, and limitations. Read
   the refreshed document and identify which operations merit discussion.
5. Run repository validation, commit the implementation, benchmark records,
   refreshed documentation, and all release artifacts/checksum entries for the
   selected version; push only after verification.

## Acceptance

- The native wheel remains CPython-tagged and contains the repository's required
  Cython extensions plus their Python sources. The universal wheel remains
  `py3-none-any` with no native extensions.
- The PyPy benchmark proves it loaded the universal wheel/package source and
  uses the same four operations and comparable fixture/repetition settings.
- PyPy results remain separate from CPython aggregate indices and do not imply
  that PyPy is embedded in a wheel or that PyPy is faster without evidence.
- No production optimization is made until the refreshed results identify a
  repeatable target; subsequent optimization gets its own evidence-backed
  change and before/after gate.
- The documented CPython and PyPy correctness tests pass, release artifacts
  verify, benchmark and diagram checks pass, and the working tree is clean
  after the requested push.

## Non-goals

- No PyPy-specific native extension wheel or hybrid CPython/PyPy wheel.
- No Cythonization of the four selected operations in this task.
- No production code change justified only by a PyPy median improvement.
- No merging PyPy latency values into CPython performance diagrams or release
  winner scores.

# Benchmark Lane Selection Design

## Goal

Allow explicit selection of the existing CPython native and universal wheel
benchmark lanes, or all three lanes including PyPy on the universal wheel,
without changing the current default benchmark behavior.

## Behavior

- With no lane selector, benchmark CPython native and CPython universal when
  both artifacts are available, as today.
- `--native` selects only the CPython native wheel.
- `--universal` selects only the CPython universal wheel. It does not imply
  PyPy.
- `--all` selects CPython native, CPython universal, and PyPy on the universal
  wheel. It requires `--pypy-python PATH` and both wheel artifacts.
- With no lane selector, `--pypy-python PATH` remains additive: it runs the
  current two CPython lanes plus the PyPy universal lane.
- Lane selectors are mutually exclusive. An explicit selector combined with
  `--pypy-python` is invalid except `--all`, preventing ambiguous lane
  selection.

## Ownership and reporting

The benchmark CLI owns selection of runtime/wheel lanes. The release wrapper
continues to own which wheel artifacts are built; its `--universal` option
remains a build option and must not be reinterpreted as a benchmark selector.
PyPy measurements remain in their separate append-only result directory and do
not enter CPython charts or aggregates. Existing defaults and saved result
reuse/overwrite protections remain unchanged.

## Validation

Add CLI and runner tests for each selector, missing required wheel/interpreter,
mutually exclusive or conflicting options, unchanged default behavior, and
separation of PyPy results. Run focused tests and the repository-required
unittest, Ruff, Pyright, and Mypy checks.

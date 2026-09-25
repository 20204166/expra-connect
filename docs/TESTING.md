# Testing

Develop and build on Linux. Platform branches are tested with injected socket,
TLS, and discovery seams. Native Windows validation remains a black-box wheel
acceptance boundary and is not replaced by Linux tests.

The suite covers identity, route ranking, full discovery lifecycle, framing,
deadlines, cancellation, TLS pinning, authenticated service round trips,
pairing direction and expiry, authorization, sharing, role fencing, cluster
membership persistence, and corruption fail-closed behavior. No test imports Tk
or System Analyzer.

Run the complete local gate with:

```sh
.venv/bin/python -m unittest discover -s tests -t .
ruff check .
ruff format --check .
pyright
.venv/bin/mypy --ignore-missing-imports src tests
```

Install editable first (`.venv/bin/pip install -e . --no-build-isolation`) so
the suite imports this checkout without `PYTHONPATH`.

The development MCP server exposes read-only diagnostics only. `run_checks`
accepts the profiles `focused`, `full`, `lint`, and `types`; it is an evidence
surface, not the source of truth. `workspace_doctor` reports
`READY_WITH_LIMITATIONS` when the configured venv lacks `pytest`, `ruff`, or
`pyright`, and the `focused` profile can exceed the MCP request timeout.

These are deterministic loopback tests. They do not replace a physical
two-machine Linux/Windows acceptance run.

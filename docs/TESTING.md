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
PYTHONPATH=src python -m unittest discover -s tests -v
ruff check .
ruff format --check .
pyright
mypy --ignore-missing-imports src tests
```

These are deterministic loopback tests. They do not replace a physical
two-machine Linux/Windows acceptance run.

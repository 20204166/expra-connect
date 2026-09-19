# Expra Connect

Expra Connect is a headless Python library for reusable peer connectivity.
It owns identity, discovery, TLS-pinned transport, pairing, authorization,
explicit capability sharing, and optional cluster membership. It does not own
Tk, hardware scanning, a game engine, or streaming.

## Development

```sh
PYTHONPATH=src python -m unittest discover -s tests -v
ruff check .
ruff format --check .
pyright
mypy --ignore-missing-imports src tests
```

The CLI demo is intentionally small:

```sh
expra-peer demo ping
expra-peer demo share
expra-peer demo loopback
```

`demo loopback` starts a local framed listener and performs an authenticated
hello request. The loopback authenticated-service integration is covered by
`tests.test_remote_service`; it starts a framed server, signs a request, and
verifies the response. The demo CLI does not advertise or pair real machines.

See `docs/ARCHITECTURE.md`, `docs/SECURITY_MODEL.md`, and
`docs/TESTING.md` for boundaries and validation rules.

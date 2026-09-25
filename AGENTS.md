# Repository Guide

Expra Connect is a headless connection library. Do not add Tk, scanner,
hardware, game-engine, or streaming dependencies. Keep discovery, pairing,
authentication, authorization, connection, sharing, and cluster membership as
separate axes.

Before changing extracted behavior, inspect the corresponding mature
implementation under `/home/btn17/Downloads/exp` and adapt its lifecycle,
security boundaries, persistence ordering, and edge-case semantics. Reuse the
proven mechanism; change only application-specific inputs and host callbacks.
For runtime, discovery, or pairing changes, compare both implementations and
add a regression test for any behavior carried across.

Never log private keys, HMAC secrets, invitation material, or fencing-token
values. A token's presence may be recorded, never its value. Do not weaken
firewalls or automatically delete persisted state.

## Canonical Boundaries

Keep discovery, pairing/trust, authentication, authorization, connection,
logical sessions, capability sharing, and cluster membership as separate axes.
Never let one substitute for another.

- `session.py` is the canonical owner of logical-session lifetime and
  connection-generation fencing. Logical sessions are process-local and
  ephemeral: never persist or restore them. A replacement socket resumes the
  same logical session; retired generations stay retired permanently (no ABA).
  `assert_current` must always check existence and expiry, including
  `generation=None`. Sessions never create trust, permissions, or authorization.
- `pairing.py` owns directional `TrustedPeer` (outbound) and `PeerGrant`
  (inbound). An inbound grant never implies outbound trust. Pairing never creates
  connection or cluster membership.
- Authorization is checked before session use and re-checked after a handler
  runs; a stale session or changed grant must not publish a result.

## Release And Packaging

- Build releases only through `scripts/build_release.sh`. It auto-selects a
  patch/feature/minor four-segment bump via `scripts/release.py prepare-build`,
  builds the wheel, verifies it, and rewrites `dist/SHA256SUMS`. Do not hand-edit
  `_version.py` except through the helper (`--bump none` for a baseline build).
- Every published release commits the wheel named in `dist/SHA256SUMS`; the
  online installer downloads exactly that file.
- The wheel must contain only `expra_connect/` and its dist-info. The MCP server
  under `tools/expra_connect_mcp/` is a development-only diagnostic surface and
  must never enter the wheel. `scripts/verify_wheel.py` enforces the headless
  surface.

## Module Size Budget

`tests/test_code_size.py` fails any `src/expra_connect/*.py` over 1000 lines.
`wire_protocol.py` and `runtime.py` sit at that limit: consolidate in place
before adding lines.

## Validation

Run the unittest suite, Ruff, Pyright, and Mypy before claiming completion.
After an editable install (`.venv/bin/pip install -e . --no-build-isolation`)
run without `PYTHONPATH`:

```sh
.venv/bin/python -m unittest discover -s tests -t .
ruff check .
ruff format --check .
pyright
.venv/bin/mypy --ignore-missing-imports src tests
```

The MCP server exposes read-only diagnostics only (`run_checks` profiles
`focused`, `full`, `lint`, `types`). Treat it as an evidence surface, never the
source of truth, and never modify `tools/` to change library behaviour.
`workspace_doctor` reports `READY_WITH_LIMITATIONS` when the configured venv
lacks optional tools (`pytest`, `ruff`, `pyright`), and the `focused` profile can
exceed the MCP request timeout on the full suite.

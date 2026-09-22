# Bidirectional Surface Harness Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use subagent-driven-development (recommended) or executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an opt-in `--bidirectional-surfaces` mode that proves both peers can pair, connect, share structured surfaces, exercise access boundaries, and clean up grants.

**Architecture:** Preserve the existing one-way `run_peer_extended.py` path. In the opt-in path, both runtimes register deterministic opaque surfaces, accept pairing requests, discover the other peer, establish independent providers, grant only explicit incoming access, and run the same redacted surface checks in both directions.

**Tech Stack:** Python 3.10+, `unittest`, `ConnectRuntime`, `SurfaceAccess`, `AuthenticatedNodeProvider`, existing ordered report writer.

---

## File Map

- Modify: `run_peer_extended.py` - bidirectional CLI flag, surface registration, reverse pairing/connection, staged checks, redacted events.
- Modify: `tests/test_run_peer_extended.py` - unit tests for callbacks, ordering, access boundaries, and cleanup.
- Modify: `examples/README.md` - two-terminal bidirectional command sequence.
- No changes: `src/expra_connect`, `run_peer.py`, or package version.

### Task 1: Add Opt-In Surface Mode And Redacted Events

**Files:** Modify `run_peer_extended.py`; modify `tests/test_run_peer_extended.py`.

- [ ] Write failing tests that the parser accepts `--bidirectional-surfaces`, one-way mode remains unchanged, and surface events record only `surface_id`, `access`, `outcome`, and `error_type`.
- [ ] Run `PYTHONPATH=src .venv/bin/python -m unittest tests.test_run_peer_extended`; expect failures for the new flag/events.
- [ ] Add `--bidirectional-surfaces` to `_parser()` with `action="store_true"`.
- [ ] Add allowlisted events `surface_request`, `surface_result`, `surface_denied`, `reverse_connected`, and `reverse_paired`.
- [ ] Add `register_harness_surfaces(runtime)` registering `desktop`, `desktop/settings`, and `device/status` with deterministic structured read/review handlers and one named action `save`.
- [ ] Add `record_surface_result(report, surface_id, access, outcome, error_type=None)` that never accepts or writes handler payloads.
- [ ] Run focused tests; expect all prior and new event tests to pass.
- [ ] Commit `feat: add bidirectional surface harness mode`.

### Task 2: Enable Pairing And Connection In Both Directions

**Files:** Modify `run_peer_extended.py`; modify `tests/test_run_peer_extended.py`.

- [ ] Write failing tests for both runtimes installing pairing callbacks, approving only the discovered peer, granting incoming read/review/action access explicitly, and shutting down both runtimes after failures.
- [ ] Add `approve_surface_pairing(runtime, report, request)` that records a redacted pairing event and returns `True` without granting any surface access; after both trust relationships exist, a separate stage grants each surface/access level explicitly.
- [ ] Configure `on_pairing_request` for both target and initiator only when `args.bidirectional_surfaces` is true; retain existing target-only approval in one-way mode.
- [ ] Add `wait_for_matching_peer(runtime, peer_id, timeout)` and `connect_bidirectionally(runtime, candidate)` so each role pairs and connects to the discovered peer, records `reverse_paired`/`reverse_connected`, and gates later surface stages.
- [ ] Ensure the target waits for the initiator candidate before reverse connection, while the initiator continues using its existing target discovery path.
- [ ] Run focused tests for callback installation, reverse connection ordering, and cleanup.
- [ ] Commit `feat: connect extended harness peers bidirectionally`.

### Task 3: Exercise Surface Access Boundaries In Both Directions

**Files:** Modify `run_peer_extended.py`; modify `tests/test_run_peer_extended.py`.

- [ ] Write failing tests for each direction: pairing-only denial, read success, review denial before grant, action denial before grant, action success after grant, stop denial, and revoke denial.
- [ ] Add `exercise_remote_surfaces(runtime, provider, peer_id, report)` with this exact sequence per direction: verify read/review/action denial immediately after pairing, grant read and verify read only, grant review and verify review, grant action and verify `save`, stop the share and verify denial, then restore only the access needed for the next independent check.
- [ ] Use provider methods `read_surface`, `review_surface`, and `invoke_surface_action`; never inspect or serialize returned payloads.
- [ ] Record only surface ID, access level, success/denial, and exception type. Catch expected `RemoteAuthorizationError` as a denial; unexpected failures return nonzero and emit only `error_type`.
- [ ] Run focused tests and verify event ordering proves both directions completed before rotation/restart stages.
- [ ] Commit `feat: exercise bidirectional surface permissions`.

### Task 4: Integrate Optional Rotation, Revocation, Docs, And Validation

**Files:** Modify `run_peer_extended.py`; modify `tests/test_run_peer_extended.py`; modify `examples/README.md`.

- [ ] Write failing tests proving rotation/reconnect leaves both providers usable only for still-granted surfaces, self-revocation denies the revoked peer, and shutdown clears session grants.
- [ ] Run existing rotation/reconnect, restart, and revoke stages after bidirectional surface checks when their flags are enabled; preserve one-way event order and cleanup behavior.
- [ ] Add a two-terminal README example using `--bidirectional-surfaces`, pinned addresses, fresh profiles, and reports. State that surfaces are structured data rather than pixel streaming and that pairing alone grants no access.
- [ ] Run `PYTHONPATH=src .venv/bin/python -m unittest tests.test_run_peer_extended` and expect all focused tests to pass.
- [ ] Run `PYTHONPATH=src .venv/bin/python -m unittest discover -s tests` and expect the full suite to pass.
- [ ] Run `PYTHONPATH=src .venv/bin/python -m compileall -q src tests scripts run_peer.py run_peer_extended.py` and `git diff --check`.
- [ ] Run Ruff, Pyright, and Mypy if installed; otherwise record they are unavailable.
- [ ] Run a real Linux/Windows bidirectional acceptance flow with fresh profiles and do not commit generated reports or profiles.
- [ ] Commit `feat: complete bidirectional surface acceptance flow` and push `main`.

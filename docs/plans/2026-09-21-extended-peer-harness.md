# Extended Peer Harness Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use subagent-driven-development (recommended) or executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a separate host-level harness that exercises discovery, pairing, connection, sharing, diagnostics, rotation, restart, and revocation through the public Expra Connect API.

**Architecture:** Copy the host-runner structure without copying library internals. The target role owns explicit pairing approval and capability registration; the initiator role advances through dependent stages and stops on classified failure. Human output is allowlisted and ordered; JSON reports remain structured and redacted.

**Tech Stack:** Python 3.10+, `unittest`, `ConnectRuntime`, `ConnectConfig`, `NodeId`, existing `run_peer.py` conventions.

---

## File Map

- Create: `run_peer_extended.py` - staged target/initiator acceptance harness.
- Create: `tests/test_run_peer_extended.py` - unit tests for stage sequencing, redaction, and failure handling.
- Modify: `examples/README.md` - document the extended command and expected stages.
- No changes: `src/expra_connect` and existing `run_peer.py`.

### Task 1: Harness Skeleton And Safe Event Writer

**Files:** Create `run_peer_extended.py`; Test `tests/test_run_peer_extended.py`.

- [ ] Write failing tests for ordered sequence numbers, report append behavior, safe event allowlisting, and cleanup on failure.
- [ ] Run `PYTHONPATH=src .venv/bin/python -m unittest tests.test_run_peer_extended`; expect import failure for the new module.
- [ ] Create the script with `argparse`, `json`, `time`, `Path`, `Lock`, `ConnectConfig`, `ConnectRuntime`, `NodeId`, and `__version__` imports.
- [ ] Implement `write_event(report, event, **values)` so terminal output contains only approved fields while JSON retains only approved structured fields. Exclude private keys, HMAC secrets, invitation material, fencing tokens, transport proofs, raw payloads, and raw exception messages.
- [ ] Implement `main()` argument parsing for `--role`, `--profile`, `--report`, `--peer-id`, `--wait`, `--advertise-address`, `--rotate-after`, `--reconnect-after-rotation`, `--rotation-wait`, `--restart-check`, and `--revoke-self`.
- [ ] Run the focused tests; expect all skeleton/event tests to pass.
- [ ] Commit `feat: add extended peer harness event writer`.

### Task 2: Target And Initiator Stages

**Files:** Modify `run_peer_extended.py`; Test `tests/test_run_peer_extended.py`.

- [ ] Write failing tests for target startup, candidate wait timeout, pairing approval, successful connection, capability result, and stage gating after failure.
- [ ] Implement target setup with `ConnectConfig`, explicit `on_pairing_request`, optional `advertised_addresses`, and `test.read_state` registration before `runtime.start()`.
- [ ] Implement initiator stages in order: start, wait for candidate, pair or restore trust, connect, request `test.read_state`, and capture `runtime.diagnostics()`.
- [ ] Record route phase, endpoint address/port/source, outcome, and measured `duration_ms`; report type-only errors.
- [ ] Ensure every stage calls `runtime.shutdown()` in `finally` and returns nonzero after a classified failure.
- [ ] Run focused tests and the local harness smoke command:
  `PYTHONPATH=src .venv/bin/python run_peer_extended.py --role target --profile /tmp/opencode/extended-target --report /tmp/opencode/extended-target.json --advertise-address 192.168.55.107`.
- [ ] Commit `feat: exercise extended pairing stages`.

### Task 3: Rotation, Restart, Revoke, Docs, And Final Validation

**Files:** Modify `run_peer_extended.py`, `tests/test_run_peer_extended.py`, `examples/README.md`.

- [ ] Write failing tests for rotation rediscovery, reconnect generation change, restart trust restoration, successful revoke denial, and malformed/missing optional state.
- [ ] Implement opt-in rotation stages using `runtime.rotate_transport()` and `runtime.reconnect_peer()`; require a changed transport generation before reporting success.
- [ ] Implement `--restart-check` without deleting profile state: shut down, create a new runtime with the same profile, verify the trusted peer remains, and report restored trust.
- [ ] Implement `--revoke-self` and require the follow-up capability request to fail with a typed denial event.
- [ ] Document target and initiator commands, expected event order, optional flags, current-wheel requirement, and report handling in `examples/README.md`.
- [ ] Run `PYTHONPATH=src .venv/bin/python -m unittest discover -s tests`; expect all tests to pass.
- [ ] Run `PYTHONPATH=src .venv/bin/python -m compileall -q src tests scripts run_peer.py run_peer_extended.py` and `git diff --check`.
- [ ] Run the canonical wheel build and `scripts/verify_wheel.py` only if package files changed; otherwise do not bump the library version for this host-only harness.
- [ ] Run the physical Linux-to-Windows flow with both nodes on the current wheel and preserve reports without committing profiles or secrets.
- [ ] Commit `feat: complete extended peer acceptance harness` and push `main`.

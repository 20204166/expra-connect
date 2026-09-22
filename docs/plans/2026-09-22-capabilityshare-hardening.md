# CapabilityShare Hardening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use subagent-driven-development (recommended) or executing-plans to implement this plan task-by-task.

**Goal:** Harden generic target-owned CapabilityShare authorization against stale grants, lifecycle leaks, invalid inputs, and concurrent revocation without changing its public model.

**Architecture:** CapabilityShare remains the sole in-memory owner of handlers and ephemeral per-peer grants. An `RLock` protects state; each grant receives a monotonic internal generation, and requests revalidate that exact generation and handler after host execution. Runtime revocation and shutdown clear generic grants, while wire validation reuses the capability ID bound.

**Tech Stack:** Python, unittest, threading events, Ruff, Pyright, Mypy.

---

### Task 1: Regression Tests First

**Files:** `tests/test_sharing.py`, `tests/test_runtime.py`, `tests/test_remote_service.py`, `tests/test_wire_protocol.py`

- [x] Add validation, per-peer isolation, revoke-peer, clear-grants, parameter identity, handler re-entry, concurrent request/revoke, revoke/re-allow ABA, unrelated mutation, and concurrent registration/allow tests.
- [x] Add runtime tests proving Pair revocation, failed-start cleanup, and same-instance shutdown/restart clear grants while preserving handlers.
- [x] Add remote tests proving Pair authorization remains the outer gate and handler failures retain `execution_failed` behavior.
- [x] Add wire tests for valid opaque IDs and IDs over 128 characters.
- [x] Run focused tests and confirm new behavior fails before implementation.

### Task 2: CapabilityShare Implementation

**File:** `src/expra_connect/sharing.py`

- [x] Add the canonical 128-character ID bound, runtime type checks, `RLock`, `revoke_peer()`, and `clear_grants()`.
- [x] Store internal per-peer capability generations; assign a new monotonic generation on each new allow transition and invalidate generations on peer/runtime clearing.
- [x] Snapshot handler, parameters, and generation under lock; invoke the handler unlocked; revalidate the same handler and generation before returning.
- [x] Preserve duplicate/unknown errors and `None` versus explicit empty mapping semantics.

### Task 3: Runtime and Wire Integration

**Files:** `src/expra_connect/runtime.py`, `src/expra_connect/wire_protocol.py`

- [x] Call `sharing.revoke_peer(peer_id)` beside surface revocation.
- [x] Call `sharing.clear_grants()` during shutdown and failed component cleanup without removing handlers.
- [x] Reuse `MAX_CAPABILITY_ID_LENGTH` in `capability_request` validation.

### Task 4: Documentation and Verification

**Files:** `docs/ARCHITECTURE.md`, `docs/SECURITY_MODEL.md`, `run_peer_extended.py`, `tests/test_run_peer_extended.py`

- [x] Document target-owned, read-only, ephemeral, Pair-subordinate CapabilityShare semantics and lifecycle clearing.
- [x] Keep the acceptance harness explicit by tracking and re-allowing only its test capability grants after its own transport rotation; do not restore grants inside the library.
- [x] Run focused suites, full unittest discovery, Ruff, format check, Pyright, Mypy, and `git diff --check`.
- [x] Verify no persistence, expiry, cluster authority, mutation API, unregister API, or duplicate sharing abstraction was added.

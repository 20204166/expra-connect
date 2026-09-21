# Generic Surface Sharing Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use subagent-driven-development (recommended) or executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add application-neutral, code-driven structured surface sharing while keeping pairing, direct authorization, cluster authorization, and application data separate.

**Architecture:** Add a typed surface registry owned by `ConnectRuntime`. Hosts register opaque surface IDs with read/review/action handlers, then grant access to individual peers or bounded cluster sources. Extend the authenticated request path with typed surface operations; keep generic `CapabilityShare` available for lower-level custom capabilities. No UI, scanner, streaming, or device-specific code is added.

**Tech Stack:** Python 3.10+, dataclasses, `unittest`, existing `ConnectRuntime`, `RemoteService`, wire protocol, `NodeId`, and cluster grant structures.

---

## File Map

- Create: `src/expra_connect/surfaces.py` - opaque surface definitions, access levels, grants, and dispatch.
- Modify: `src/expra_connect/runtime.py` - public registration/grant/revoke/stop methods and runtime lifecycle ownership.
- Modify: `src/expra_connect/remote_service.py` - target-side surface request dispatch and typed provider methods.
- Modify: `src/expra_connect/wire_protocol.py` - versioned operation metadata and parameter validation.
- Modify: `tests/test_sharing.py` - registry unit tests.
- Modify: `tests/test_remote_service.py` - authenticated surface request tests.
- Modify: `tests/test_runtime.py` - public runtime seam and lifecycle tests.
- Modify: `tests/test_wire_protocol.py` - operation validation and compatibility tests.
- Modify: `examples/README.md` - application-neutral usage example.
- No changes: `run_peer.py`, `run_peer_extended.py`, or any UI/application code.

### Task 1: Define Surface Registry And Access Semantics

**Files:** Create `src/expra_connect/surfaces.py`; modify `tests/test_sharing.py`.

- [ ] Write failing tests for opaque IDs, read/review/action registration, duplicate rejection, direct peer grants, stop/revoke, expiry, and handler errors.
- [ ] Run `PYTHONPATH=src .venv/bin/python -m unittest tests.test_sharing`; expect missing surface types/registry failures.
- [ ] Add `SurfaceAccess` values `read`, `review`, and `action`; add immutable `SurfaceDefinition` and `SurfaceGrant` records.
- [ ] Implement `SurfaceRegistry.register(surface_id, *, read, review=None, actions=None)` with non-empty bounded IDs and no duplicate surfaces/actions.
- [ ] Implement `grant_peer`, `revoke_peer`, `stop_peer`, `grant_cluster`, and `dispatch` so every request checks source, surface, access, expiry, and registered handler before invocation.
- [ ] Keep grants in memory by default. Do not serialize surface grants with pairing trust.
- [ ] Run focused tests; expect all registry tests to pass.
- [ ] Commit `feat: add generic surface registry`.

### Task 2: Integrate Runtime And Cluster Authorization

**Files:** Modify `src/expra_connect/runtime.py`; modify `src/expra_connect/remote_service.py`; modify `tests/test_runtime.py` and `tests/test_remote_service.py`.

- [ ] Write failing tests for `register_surface`, `grant_surface_access`, `revoke_surface_access`, `stop_surface_share`, cluster grants, runtime shutdown cleanup, and peer self-revocation.
- [ ] Add one `SurfaceRegistry` to `ConnectRuntime` and pass it into `RemoteService` without changing existing `CapabilityShare` behavior.
- [ ] Expose these public methods:

```python
def register_surface(self, surface_id, *, read, review=None, actions=None):
    self._surface_registry.register(surface_id, read=read, review=review, actions=actions)

def grant_surface_access(self, peer_id, surface_id, *, access, expires_at=None):
    self._surface_registry.grant_peer(peer_id, surface_id, access=access, expires_at=expires_at)

def revoke_surface_access(self, peer_id, surface_id, *, access=None):
    self._surface_registry.revoke_peer(peer_id, surface_id, access=access)

def stop_surface_share(self, peer_id, surface_id):
    self._surface_registry.stop_peer(peer_id, surface_id)
```

- [ ] Make shutdown clear non-persistent surface grants and make self-revocation invalidate direct and cluster-derived surface access.
- [ ] Feed existing cluster grant/fencing state into the registry as a bounded authorization source; cluster membership alone must not grant a surface.
- [ ] Run `PYTHONPATH=src .venv/bin/python -m unittest tests.test_runtime tests.test_remote_service`; expect all tests to pass.
- [ ] Commit `feat: expose runtime surface sharing seams`.

### Task 3: Add Typed Wire Operations And Client Provider API

**Files:** Modify `src/expra_connect/wire_protocol.py`; modify `src/expra_connect/remote_service.py`; modify `tests/test_wire_protocol.py` and `tests/test_remote_service.py`.

- [ ] Write failing tests for `surface_read`, `surface_review`, and `surface_action` operation metadata, required parameters, unknown surface/action rejection, and old operation compatibility.
- [ ] Add versioned operation entries and strict parameter validation for `surface_id`, optional section data, action name, and JSON-safe params.
- [ ] Dispatch target requests through `SurfaceRegistry` using authenticated peer identity and cluster context; never let request parameters choose an unregistered handler.
- [ ] Add provider methods:

```python
def read_surface(self, surface_id, *, params=None):
    return self._request("surface_read", {"surface_id": surface_id, "params": dict(params or {})})

def review_surface(self, surface_id, *, params=None):
    return self._request("surface_review", {"surface_id": surface_id, "params": dict(params or {})})

def invoke_surface_action(self, surface_id, action, *, params=None):
    return self._request(
        "surface_action",
        {"surface_id": surface_id, "action": action, "params": dict(params or {})},
    )
```

- [ ] Return structured host-owned data and map denied, expired, revoked, malformed, and handler failures to existing typed remote errors without raw sensitive exception text.
- [ ] Run focused protocol/service tests and `PYTHONPATH=src .venv/bin/python -m unittest tests.test_wire_protocol tests.test_remote_service`.
- [ ] Commit `feat: add typed surface request APIs`.

### Task 4: Documentation, Regression Matrix, And Validation

**Files:** Modify `examples/README.md`; create `tests/test_surface_integration.py`.

- [ ] Create `tests/test_surface_integration.py` with a two-runtime integration test proving pairing alone denies a surface, explicit read grant succeeds, review/action remain denied, and a named action succeeds only after its grant.
- [ ] Extend `tests/test_surface_integration.py` with arbitrary nested surface IDs, direct peer grants, cluster-bounded grants, expiry, stop, restart cleanup, and revocation cases.
- [ ] Document code-driven registration/grant/revoke examples and state clearly that surfaces are structured data, not pixel streaming.
- [ ] Run `PYTHONPATH=src .venv/bin/python -m unittest discover -s tests` and expect the full suite to pass.
- [ ] Run `PYTHONPATH=src .venv/bin/python -m compileall -q src tests scripts run_peer.py run_peer_extended.py`.
- [ ] Run `git diff --check` and inspect `git status --short`; do not add generated profiles or reports.
- [ ] Run `ruff`, `pyright`, and `mypy` if installed; otherwise record that they are unavailable.
- [ ] Do not bump the package version unless published protocol compatibility requires it; if wire changes require a version decision, stop before release packaging.
- [ ] Commit `docs: document generic surface sharing` and push `main` after review.

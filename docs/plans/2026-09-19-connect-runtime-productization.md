# ConnectRuntime Productization Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use subagent-driven-development (recommended) or executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Provide a headless `ConnectRuntime` façade and release surface that preserves the mature System Analyzer lifecycle without duplicating networking code.

**Architecture:** `ConnectRuntime` composes the existing identity, persistence, TLS, registry, `RemoteSocketServer`, `NetworkDiscovery`, pairing, sharing, and optional cluster components. It owns ordering, lifecycle generation, structured status, and application-neutral callbacks; the existing components retain protocol and security authority.

**Tech Stack:** Python 3.10+, unittest, setuptools, cryptography, zeroconf, Ruff, Pyright, Mypy.

---

## File Map

- Create `src/expra_connect/runtime.py`: `ConnectConfig`, lifecycle/status models, and the composition root.
- Modify `src/expra_connect/discovery_full.py`: parameterize service type and preserve candidate parsing against that type.
- Modify `src/expra_connect/__init__.py`: stable façade exports plus explicit advanced exports.
- Modify `src/expra_connect/cli.py`: library-backed `--help`, `--version`, and demos.
- Create `src/expra_connect/_version.py`: canonical package version.
- Modify `pyproject.toml`: dynamic version loading and release metadata.
- Create `tests/test_runtime.py`: façade tests with injected listener/discovery seams.
- Modify `tests/test_discovery_full.py`: configurable service-type tests.
- Create `tests/test_packaging.py`: source boundary, CLI, and version tests.
- Create `scripts/build_release.sh`: build, inspect, and checksum artifacts.
- Create `scripts/verify_wheel.py`: wheel-content and dependency-boundary verifier.
- Create `install.sh`: checksum-verifying POSIX installer.
- Create `install.ps1`: checksum-verifying Windows installer.
- Create `docs/EXPRA_ENGINE_INTEGRATION.md`: TkDeliveryQueue integration contract.
- Modify `README.md`: high-level API and lifecycle usage.

## Task 1: Parameterize Discovery

**Files:** `src/expra_connect/discovery_full.py`, `tests/test_discovery_full.py`

- [ ] Add a failing test that constructs `NetworkDiscovery` with `service_type="_custom._tcp.local."`, sends a matching synthetic service name, and asserts the candidate is accepted while the old service type is ignored.
- [ ] Run `python -m unittest tests.test_discovery_full -v`; confirm the new test fails because the constructor and normalizer use the module constant.
- [ ] Add a `service_type` constructor argument defaulting to `SERVICE_TYPE`, pass it to the backend advertisement seam, and use it for service-name parsing. Keep Zeroconf backend creation and all candidate validation unchanged.
- [ ] Run the focused discovery tests and then `ruff check src/expra_connect/discovery_full.py tests/test_discovery_full.py`.
- [ ] Commit `feat: parameterize discovery service type`.

## Task 2: Runtime Status and Configuration

**Files:** `src/expra_connect/runtime.py`, `tests/test_runtime.py`

- [ ] Write failing tests for construction having no socket/discovery effects, defaults (`discovery_enabled=True`, `bind_host="0.0.0.0"`, preferred port `27321`), profile paths, and cluster disabled by default.
- [ ] Run the focused tests and confirm the missing `ConnectConfig`/`ConnectRuntime` failure.
- [ ] Implement immutable `ConnectConfig` validation and status models with explicit states for stopped, starting, started, listener failure, persistence failure, discovery disabled, and discovery unavailable. Store only paths and configuration during construction.
- [ ] Run `python -m unittest tests.test_runtime -v` and type-check the new module.
- [ ] Commit `feat: add runtime configuration and status models`.

## Task 3: TDD Runtime Startup

**Files:** `src/expra_connect/runtime.py`, `tests/test_runtime.py`

- [ ] Add failing seam-based tests proving identity is saved before discovery, listener starts before discovery, advertisement uses actual bound port and active TLS fingerprint, preferred-port status is exposed, and fallback-port status is exposed.
- [ ] Add failing tests for persistence failure, listener failure, unavailable/disabled discovery, and cleanup of partial startup.
- [ ] Implement injectable factories for identity loading/saving, TLS material, server, discovery, and clock/timer delivery. Compose the existing `RemoteSocketServer` and `NetworkDiscovery`; do not copy their protocol logic.
- [ ] Implement startup in the exact order: durable identity, persisted state, TLS, listener, actual endpoint, discovery, reconciliation.
- [ ] Run the focused runtime tests and inspect that no advertisement is emitted on failed persistence or listener startup.
- [ ] Commit `feat: compose mature runtime startup lifecycle`.

## Task 4: TDD Idempotent Stop and Generation Fencing

**Files:** `src/expra_connect/runtime.py`, `tests/test_runtime.py`

- [ ] Add failing tests for `start()` twice returning the existing status without duplicate infrastructure, `shutdown()` twice being safe, start-stop-start creating one current lifecycle, and old callbacks being ignored after restart.
- [ ] Implement a monotonically increasing generation captured by every discovery/reconciliation/expiry callback. Invalidate it before cancellation and stop discovery before the listener.
- [ ] Expose `shutdown()` and context-manager methods; preserve profile files and trust/grant state.
- [ ] Run all runtime tests, including thread-safe callback delivery tests, and commit `feat: add fenced runtime shutdown lifecycle`.

## Task 5: Compose Public Services

**Files:** `src/expra_connect/runtime.py`, `src/expra_connect/__init__.py`, `tests/test_runtime.py`

- [ ] Add failing tests showing peer discovery state is distinct from paired, granted, connected, authorized, and cluster-member state; test profile separation and restart identity/TLS/trust reuse.
- [ ] Expose read-only peer/status accessors and composition methods that delegate to existing pairing, sharing, registry, and optional cluster owners without inferring authorization from connection state.
- [ ] Export stable façade symbols and place low-level mechanisms in an explicit advanced namespace/list.
- [ ] Run boundary and runtime tests; commit `feat: expose stable connect runtime API`.

## Task 6: Version, CLI, and Release Artifacts

**Files:** `_version.py`, `pyproject.toml`, `cli.py`, `scripts/build_release.sh`, `scripts/verify_wheel.py`, `tests/test_packaging.py`

- [ ] Add failing tests for `python -m expra_connect.cli --help`, `--version`, dynamic package version, and rejection of wheel contents importing Tk/System Analyzer/Expra Engine.
- [ ] Add `_version.py`, configure setuptools dynamic version, and make CLI use the library version without network activity for help/version.
- [ ] Implement release build/checksum scripts and wheel verifier; ensure the verifier checks package paths, metadata, and forbidden imports.
- [ ] Run CLI tests, `python -m build`, verifier, and `unzip -l` inspection; commit `build: add versioned wheel release tooling`.

## Task 7: Installers and Integration Documentation

**Files:** `install.sh`, `install.ps1`, `README.md`, `docs/EXPRA_ENGINE_INTEGRATION.md`

- [ ] Document clean virtual-environment installation, checksum verification, explicit `runtime.start()`, callback thread ownership, profile ownership, and TkDeliveryQueue handoff.
- [ ] Implement installers that download a release wheel and `SHA256SUMS`, verify the selected artifact before installation, and never alter firewall or persisted state.
- [ ] Run shell syntax checks, PowerShell parse checks where available, and documentation link/package command checks.
- [ ] Commit `docs: add installation and Expra Engine integration guidance`.

## Task 8: Final Verification

- [ ] Run `scripts/run_tests.sh` or the repository unittest equivalent for the full suite.
- [ ] Run `ruff check .`, `ruff format --check .`, `pyright`, and `mypy --ignore-missing-imports`.
- [ ] Build the wheel, verify it, install it into a fresh virtual environment, and run `expra-peer --help`, `expra-peer --version`, and the loopback demo.
- [ ] Run `git diff --check`, inspect `git status`, and review the complete diff for secrets, forbidden dependencies, false endpoint advertisement, and accidental source-tree imports.

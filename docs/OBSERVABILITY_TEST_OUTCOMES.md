# Observability Test Outcomes

Date: 2026-09-21
Commit: `0d947dd`

## Test Results

The repository test suite completed successfully:

```text
Ran 243 tests in 26.214s
OK
```

The run emitted expected environment/test warnings:

- Zeroconf IPv6 startup fell back to IPv4 in the discovery test.
- A persistence test intentionally exercised a disk-full failure path.
- The code-size test reported three modules above the soft 900-line threshold; none exceeded the 1000-line hard limit.

Ruff, Pyright, and Mypy were unavailable in the environment.

## Instrumented Scenario

The scenario used one shared `ObservabilityWatcher` for an
`AuthenticatedNodeProvider` and one for its `RemoteService`. It exercised:

1. A successful `hello` request.
2. A cancelled `hello` request.
3. A request after provider invalidation.
4. Runtime start and shutdown with discovery disabled.

Observed metrics:

| Target | Count | Successes | Failures | Cancellations | In flight |
| --- | ---: | ---: | ---: | ---: | ---: |
| `remote:hello` | 3 | 1 | 1 | 1 | 0 |
| `service:hello` | 1 | 1 | 0 | 0 | 0 |

Runtime started successfully with state `started`. Its watcher had no metrics
after start and shutdown because runtime lifecycle events are not currently
instrumented.

## Currently Observed

- Authenticated client-side remote operations through `AuthenticatedNodeProvider`.
- Server-side remote operation execution through `RemoteService`.
- Outgoing connection route attempts through `ConnectionManager`.
- Runtime diagnostics through `ConnectRuntime.diagnostics()`.
- Direct watcher operations, bounded samples, outcomes, event counters, reset, and concurrency behavior through `tests/test_observability.py`.
- Profile lock acquisition/release, profile identity load/create, legacy identity
  migration, and device identity migration through stable operation targets.
  Targets omit profile paths, NodeIds, and key material; regression coverage is
  in `tests/test_profile_observability.py`.

## Areas Needing Work

| Area | Current state | Suggested next measurement |
| --- | --- | --- |
| Runtime lifecycle | Profile lock acquisition/release is measured; overall start/shutdown and listener/discovery outcomes are not. | Add lifecycle outcome metrics without duplicating profile-lock durations. |
| Discovery | Discovery events are not recorded by the shared watcher. | Record discovery start, candidate accepted/rejected, stale candidate, and stop outcomes. |
| Pairing | Pairing and elevation flows are not individually observed. | Record request, approval/rejection, timeout, cancellation, and persistence failure. |
| Profile identity persistence | Load/create and legacy split-format migrations are measured; profile lock contention is counted as failure. | Preserve these stable targets; add no path or identity labels. |
| Other persistence | Trust, transport, idempotency, and cluster load/save are not measured. | Record operation duration and failure outcome without recording state contents. |
| Registry and sharing | Registry transitions and capability-share calls are not measured. | Record promote/revoke/share decisions using stable operation names only. |
| Local cluster operations | Local role, failover, and membership transitions are not measured. | Add metrics at role/failover boundaries, preserving fencing-token secrecy. |
| Diagnostics consumption | Metrics are exposed in the runtime diagnostics dictionary, but no exporter or retention policy exists. | Define the host-facing export and sampling/reset lifecycle. |

## Interpretation

The watcher is operationally proven for the remote connection path, but it is
not yet a whole-repository activity ledger. The next work should instrument
shared lifecycle boundaries rather than every helper, so metrics remain bounded,
non-sensitive, and free of double-counting.

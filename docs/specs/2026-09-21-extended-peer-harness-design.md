# Extended Peer Harness Design

## Purpose

Add a separate host-level acceptance harness that exercises the current public
Expra Connect API more deeply than `run_peer.py`, without changing the library
or breaking the existing acceptance command.

## Scope

The new `run_peer_extended.py` harness will support the same `target` and
`initiator` roles and stage the following flow:

1. Start the runtime and report version, status, and diagnostics.
2. Discover a compatible peer and report endpoint candidates.
3. Pair with explicit `read_state` permissions.
4. Connect and report route latency, TLS verification, and transport generation.
5. Request the target-owned shared capability.
6. Capture diagnostics and observability metrics.
7. Optionally rotate the target transport generation.
8. Rediscover and reconnect after rotation.
9. Request the shared capability after reconnect.
10. Revoke the caller and verify a subsequent request is denied.
11. Optionally restart the initiator profile and verify persisted trust.

Each stage will produce structured report events and ordered human-readable
terminal output. Failures will identify the boundary involved: discovery,
pairing, transport, authentication, authorization, persistence, or rotation.

## Safety Boundaries

- No changes to `src/expra_connect` are required for the harness.
- Existing `run_peer.py` behavior and report shape remain unchanged.
- Terminal output keeps addresses, ports, endpoint sources, versions, route outcomes, latency, permissions, and abbreviated peer IDs.
- Reports omit private keys, HMAC secrets, invitation material, fencing tokens, transport proofs, raw request payloads, and raw exception details.
- Target capability registration remains explicit and read-only.
- The harness never disables firewalls or deletes persisted state.
- Rotation and restart stages are opt-in flags, not implicit destructive actions.

## Architecture

The harness will use only public package entry points: `ConnectConfig`,
`ConnectRuntime`, `NodeId`, runtime pairing/connection methods, capability
sharing, diagnostics, and provider operations. It will not import private
library helpers or duplicate connection/security logic.

The target role owns the pairing approval callback and registers
`test.read_state` before starting discovery. The initiator role waits for a
current compatible candidate, performs the staged operations, and records each
result. A single process-local event writer will maintain ordered terminal
sequence numbers and append structured events to the selected report.

## Failure Handling

Each stage stops subsequent dependent stages after failure and writes one
classified error event. Cleanup always shuts down the local runtime. A failed
rotation must not delete trust or profile state. A failed revoke verification
is a hard failure because it would indicate an authorization regression.

## Validation

- Add focused tests for event ordering, stage gating, optional rotation/restart paths, failure classification, report redaction, and cleanup.
- Run the full unittest suite.
- Run a local target/initiator-compatible smoke path where possible.
- Run the physical Linux-to-Windows acceptance flow with both sides on the current wheel.
- Run compile, diff, wheel, and clean-install checks when packaging inputs change.

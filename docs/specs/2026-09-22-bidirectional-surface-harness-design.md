# Bidirectional Surface Harness Design

## Goal

Extend `run_peer_extended.py` with an opt-in bidirectional mode that exercises
both peers as authenticated callers and target-owned surface providers without
changing the existing one-way acceptance flow.

## Activation

The new mode is enabled with:

```text
--bidirectional-surfaces
```

Without this flag, target and initiator behavior remains compatible with the
current discovery, pairing, connection, rotation, restart, and revocation
test.

## Bidirectional Lifecycle

Both runtimes will:

1. Start with a pairing-request callback.
2. Register the same harness-owned structured surfaces.
3. Discover the other peer.
4. Pair and connect in both directions.
5. Grant only the explicit incoming peer surface access.
6. Exercise reads, reviews, and one named action against the remote peer.
7. Stop and revoke selected access and verify denial.
8. Optionally rotate/reconnect and verify only intended grants remain.
9. Shut down and verify session grants are not restored automatically.

The target remains the process that starts first, but it also becomes an
initiator of a reverse authenticated connection in this mode. The initiator
also accepts the target's pairing request, so each side has an independent
trusted relationship and provider.

## Surfaces And Access

The harness registers opaque structured surfaces:

- `desktop`
- `desktop/settings`
- `device/status`

Each side exposes read and review handlers plus a named action. The handlers
return deterministic test data, but reports record only surface ID, access
level, outcome, and error type. Payload data is never printed or persisted by
the harness.

The test must prove that pairing alone denies access, read does not imply
review or action, and stopping/revoking access blocks subsequent requests.

## Observability

New redacted events include surface operation, access level, outcome, and typed
error events. Existing route, pairing, connection, rotation, restart, and
revocation events remain ordered. No private keys, HMAC secrets, invitation
material, fencing tokens, transport proofs, raw payloads, or raw exception text
may appear in terminal output or reports.

## Failure And Cleanup

Each side must shut down its runtime in `finally`, and failures must gate later
stages. Reverse pairing, reverse connection, surface operations, and denial
checks must each have focused tests. Existing one-way tests and `run_peer.py`
must remain unchanged.

## Out Of Scope

- Pixel-level desktop streaming.
- Keyboard, pointer, or touch injection.
- UI code or Expra-specific analyser data.
- Automatic surface access from pairing or cluster membership.

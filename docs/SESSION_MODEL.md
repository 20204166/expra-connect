# Logical Session Model

`session.py` (`LogicalSessionRegistry`) is the canonical owner of
authenticated logical-session lifetime and connection-generation fencing. It is
deliberately small and does not own pairing, connection, transport, replay,
idempotency, or capability sharing.

## Model

An authenticated logical session is process-local and ephemeral:

- owner: the authenticated identity (`caller_node_id`, else `node_id`)
- active generation: one opaque `connection_generation` token (`None` allowed)
- expiry: an absolute `expires_at`
- retired generations: a permanent set of previously active tokens

A physical connection replacement does not create a new session. It resumes the
same session (`resume=True`) and retires the previous generation:

```text
generation A --resume--> generation B
B = current, A = permanently retired
```

Generations are opaque: only equality, currency, and retirement are meaningful.
No ordering, parsing, route, address, or transport knowledge belongs here.

## Invariants

- `assert_current(session_id, generation)` always checks that the session exists
  and has not expired, including when `generation=None` (no external socket
  generation). It then requires the generation to equal the active one.
- Expiry is absolute: ordinary use and resume never extend it.
- Retired generations stay retired permanently; `A -> B -> A` is denied (no ABA).
- A retired-generation request is side-effect free: it cannot change the active
  generation, retired set, expiry, owner, or session existence.
- Same-generation resume is an idempotent no-op and adds no retirement entry.
- A generation change without `resume=True` is rejected as stale.
- Owner mismatch is rejected and leaves the original session unchanged.
- Unknown and expired sessions fail with the same controlled error.
- The active-session count is bounded; expired sessions are pruned lazily before
  the capacity check, and a still-full registry fails closed instead of evicting
  a healthy session.
- Per-session retired history is bounded. On reaching the bound the session is
  invalidated, so a forgotten retired generation can never become reusable.
- Session IDs are unpredictable (`secrets.token_hex(16)`, 128 bits); creation is
  collision-safe and never overwrites a live session.
- TTL must be a finite positive number (`bool`/non-number rejected; `<=0`, NaN,
  and infinities rejected). A clock reading that is not a finite number fails
  closed.

## Clock

Session duration is an elapsed-duration problem, so the registry clock defaults
to `time.monotonic`. `RemoteService` keeps its wall-clock `clock` for request
timestamp/freshness and passes a separate `session_clock` for session duration.
Tests inject fake clocks for both.

## Boundaries

- Sessions never create trust, permissions, or authorization. Pair/grant
  authorization is checked before session use and re-checked after a handler
  runs; a revoked grant cannot be bypassed with a valid session.
- Sessions are never persisted and never restored; a service restart requires a
  fresh session.
- Request replay, nonces, freshness, and idempotent result reuse belong to
  `ReplayCache` and `IdempotencyCache`, not to the session registry.
- Online/offline state, routes, and reconnect belong to `ConnectionManager`.
- The wire boundary (`wire_protocol.py`) validates session/generation shape,
  enforces the canonical identifier length, and rejects `resume=True` without a
  `session_id`.

## Tests

Low-level behavior lives in `tests/test_session.py`; end-to-end wiring (session
issue, resume across generations, stale in-flight result rejection, restart,
grant revocation) lives in `tests/test_remote_service.py`.

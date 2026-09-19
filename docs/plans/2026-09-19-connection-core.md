# Connection Core Extraction Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use subagent-driven-development (recommended) or executing-plans to implement this plan task-by-task.

**Goal:** Build an independently installable headless peer-connection repository without modifying System Analyzer.

**Architecture:** Pure identity, discovery, protocol, TLS, transport, pairing, sharing, and cluster modules use explicit persistence and callback seams. Application UI, scanners, and handlers remain outside the package.

**Tech Stack:** Python 3.10+, `cryptography`, optional Zeroconf, unittest, Ruff, Pyright, and Mypy.

---

### Tasks

- [ ] Implement identity and persistence-safe value types.
- [ ] Implement protocol framing, HMAC envelopes, and replay freshness.
- [ ] Implement TLS material, fingerprint pinning, socket deadlines, and cancellation.
- [ ] Implement discovery candidates, validation, ranking, TTL, and self-filtering.
- [ ] Implement directional pairing records, pending approval, confirmation, abort, and expiry.
- [ ] Implement generic capability sharing and authorization.
- [ ] Implement invite/join, role assignments, coordinator epochs, and offline membership.
- [ ] Add CLI demo and import-boundary tests.
- [ ] Run all quality gates and verify the source repository is untouched.

## Stable Identity, Rotating Transport & Session Continuity

**Status:** Future work. This section records the intended design boundary only;
it does not authorize implementation in the current extraction phase.

The future model keeps `NodeId` as the stable logical peer identity while
treating addresses, ports, TLS credentials, routes, sockets, and sessions as
replaceable layers. Transport generations may rotate only through authenticated
continuity from the already-trusted identity. A peer may advertise several
validated endpoint candidates, and one canonical path manager may migrate or
fail over between them without changing trust, pairing, or cluster membership.
Logical sessions should eventually survive socket replacement where safely
resumable, with request IDs and bounded idempotency/replay handling preventing
mutating operations from executing twice after a lost response. Relay, NAT
traversal, streaming, and speculative multipath transport remain out of scope.

The exact future implementation prompt is preserved below for later use:

```text
EXPRA CONNECT — STABLE IDENTITY, ROTATING TRANSPORT & SESSION CONTINUITY

Implement the planned "Stable Identity, Rotating Transport & Session Continuity"
work in the CURRENT expra-connect repository.

Do not treat this prompt as permission to redesign the mature connection core.

The central invariant is:

    IDENTITY IS STABLE.
    PROOFS MAY ROTATE.
    ROUTES MAY MOVE.
    SESSIONS MAY RESUME.

======================================================================
0. USE THE AVAILABLE ENGINEERING SKILLS
======================================================================

Before changing code, inspect the skills available to this coding agent and use
all relevant ones.

At minimum, if available, explicitly use:

    brainstorming
        for design alternatives and threat/failure analysis

    consolidating-responsibilities
        to ensure each new responsibility has one canonical owner and
        ConnectRuntime does not become a god object

    writing-plans / planning
        to break implementation into safe phases before coding

    test-driven-development
        to add failing regression tests before fixes for each edge case

    systematic-debugging
        whenever an existing behavior/test fails during integration

    verification-before-completion
        for final tests, wheel checks, clean install, and evidence-based report

If skill names differ in this environment, use their closest available
equivalents.

Do not skip relevant skills merely to move faster.

======================================================================
1. AUDIT BEFORE DESIGN
======================================================================

Inspect the CURRENT source, tests, docs and Git history first.

Determine the canonical existing owners for:

    NodeId / identity
    identity fingerprints
    transport/TLS fingerprints
    TLS material
    peer/trust records
    discovery candidates
    endpoint reconciliation
    connection lifecycle/retry
    Pairing
    session/request protocol
    persistence
    ConnectRuntime composition

SEARCH BEFORE CREATE.

Prefer extending existing mature owners over creating parallel managers.

Use the consolidating-responsibilities process before introducing any new class.

======================================================================
2. DO NOT ROTATE NODEID
======================================================================

NodeId remains the durable logical peer identity.

It must not change because of:

    IP change
    port change
    Wi-Fi change
    IPv4 -> IPv6
    VPN appearance/disappearance
    reconnect
    TLS transport-key rotation
    process restart

Do not redefine NodeId into an endpoint/session identifier.

======================================================================
3. ROOT IDENTITY VS TRANSPORT IDENTITY
======================================================================

Formalize the distinction between:

    stable root identity

and:

    replaceable transport credentials

Conceptually:

    NodeId
        |
        +-- stable identity key / continuity authority
        |
        +-- transport generation N
        +-- transport generation N+1

A new TLS/transport credential must NOT become trusted merely because it claims
the same NodeId.

There must be authenticated continuity from the already-trusted identity.

Research the most appropriate design for the CURRENT Expra architecture before
choosing the mechanism.

Potential concept:

    identity key signs/authorizes the next transport generation

but do not blindly implement this exact shape if another Expra-native design is
cleaner and safer.

======================================================================
4. TRANSPORT ROTATION
======================================================================

Support safe transport credential rotation without destroying peer trust.

Handle:

    current generation
    next generation
    activation
    retirement
    short overlap/grace period where justified
    restart persistence
    rollback/failure

Security requirement:

    known NodeId + unrelated new TLS key
        MUST NOT
    silently replace the trusted transport identity.

Test this explicitly.

======================================================================
5. MULTI-ENDPOINT PEER MODEL
======================================================================

A peer should be able to have multiple route candidates.

Examples:

    LAN IPv4
    LAN IPv6
    VPN
    configured endpoint
    future relay

Do not make:

    peer.host
    peer.port

the peer's identity.

If current storage assumes one endpoint, evolve it carefully with backwards
compatibility.

Each endpoint may need metadata such as:

    source
    last_seen
    validation state
    priority/preference
    last success/failure

Do not over-design speculative fields.

======================================================================
6. PATH SELECTION / FAILOVER
======================================================================

Create or extend ONE canonical connection/path owner.

Do NOT put route scoring/failover logic directly into ConnectRuntime.

It should be possible for one logical peer to move:

    LAN IPv4
        ->
    IPv6
        ->
    VPN

without changing:

    NodeId
    trust
    Pair status
    cluster membership

The host should see useful logical states such as:

    CONNECTED
    MIGRATING / RESUMING
    OFFLINE

where supported by the existing state model.

Do not promise "never disconnect."

If every path is unavailable, the peer is offline.

======================================================================
7. LOGICAL SESSION != SOCKET
======================================================================

Audit the existing protocol before changing it.

Introduce a logical-session boundary only if it can be integrated cleanly.

Target idea:

    logical peer session
        |
        +-- connection generation 1 / socket A
        +-- connection generation 2 / socket B

A transient socket replacement should not necessarily destroy higher-level
application continuity.

Preserve authentication on every new physical transport.

Session resumption must never bypass identity/auth checks.

======================================================================
8. REQUEST IDS / IDEMPOTENCY
======================================================================

Research and address this failure:

    peer sends mutating request
        ->
    target commits operation
        ->
    response is lost
        ->
    client reconnects
        ->
    client retries

The target must not accidentally execute the same operation twice merely because
the response was lost.

Design Expra-native:

    request ID / operation ID
    replay detection
    cached committed result where appropriate
    bounded retention/expiry

Do not make every read-only request unnecessarily durable.

Differentiate:

    idempotent read
    retry-safe mutation
    non-retry-safe operation

======================================================================
9. ROTATING SESSION MATERIAL
======================================================================

Investigate whether session-level ephemeral keys/key generations belong in this
phase given the CURRENT protocol.

If justified, design them independently of root identity and TLS transport
identity.

If not justified yet:

    document the seam
    do not invent crypto complexity.

Never implement custom cryptographic primitives where established library
primitives should be used.

======================================================================
10. EXTERNAL RESEARCH — BORROW FAILURES, NOT IMPLEMENTATIONS
======================================================================

Research mature open-source projects for failure cases and tests.

Relevant projects include:

    Syncthing
    Tailscale
    libp2p
    Sunshine
    RustDesk
    LocalSend

Use:

    source
    tests
    issues
    security fixes
    protocol docs
    bug-fix history

The purpose is:

    WHAT BROKE FOR THEM?

NOT:

    COPY THEIR ARCHITECTURE.

Do not port their:

    classes
    names
    state machines
    protocols
    routing architecture

unless a tiny generic technique is independently justified and license-safe.

For every relevant external lesson:

    identify analogous Expra failure
        ->
    determine whether already covered
        ->
    add Expra regression test
        ->
    implement using Expra's own NodeId/trust/connection model

Document research sources in:

    docs/EDGE_CASE_RESEARCH.md

======================================================================
11. ACTIVELY RESEARCH THESE EDGE CASES
======================================================================

At minimum cover/research:

IDENTITY / ROTATION

    trusted peer rotates transport credential while remote peer is offline
    rotation occurs during active connection
    restart during rotation
    old generation used after retirement
    unauthorized new fingerprint claims known NodeId
    rollback after failed rotation
    two rotations race
    corrupted persisted generation metadata

ROUTES

    IP changes while NodeId remains same
    IPv4 disappears but IPv6 works
    VPN appears/disappears
    stale endpoint remains in discovery
    several endpoints advertised simultaneously
    preferred path fails during request
    old endpoint becomes reachable again
    duplicate discovery updates
    same hostname but different NodeId
    same NodeId simultaneously observed at conflicting endpoints

CONNECTION

    both peers initiate simultaneously
    duplicate outgoing connection attempts
    active route disappears
    failover candidate succeeds
    all candidates fail
    authentication fails on replacement route
    fingerprint mismatch on replacement route
    stale callback from previous connection generation

SESSION

    socket dies after authenticated session creation
    resume succeeds on new socket
    resume attempt from wrong identity
    expired session
    duplicate resume
    old connection sends message after migration

REQUESTS

    response lost after successful mutation
    retry after reconnect
    duplicate request received concurrently
    duplicate request after restart where durability requires detection
    request ID collision/malformed ID
    replay of authenticated request
    in-flight permission revoked

LIFECYCLE

    shutdown while migration occurs
    shutdown during rotation
    restart during pending reconnect
    start/stop/start stale callbacks
    persistence failure during security-state update

======================================================================
12. TEST-FIRST
======================================================================

For every CURRENT relevant gap:

1. write the regression test first
2. demonstrate the missing behavior
3. implement smallest architecture-correct change
4. rerun related tests
5. run full suite

Do not add speculative implementation merely because another project has it.

Classify external findings:

    A — already covered
    B — implementation exists, test missing
    C — current real gap
    D — future seam only
    E — not applicable

Only B/C normally change current code.

======================================================================
13. BACKWARDS COMPATIBILITY
======================================================================

Existing persisted profiles may contain:

    one transport fingerprint
    one endpoint
    older schema

Do not casually make them unreadable.

If schema evolves:

    version it
    migrate safely
    preserve malformed-state fail-soft behavior

Add migration tests.

======================================================================
14. SECURITY INVARIANTS
======================================================================

Must remain true:

    NodeId is not an IP address.

    endpoint discovery is not authentication.

    knowing NodeId is not authorization.

    new TLS fingerprint is not accepted solely because NodeId matches.

    Pair != connection.

    Pair != cluster Join.

    trust survives route changes.

    route changes do not silently change identity.

    session resumption cannot bypass authentication.

    replay protection cannot become an unbounded memory store.

======================================================================
15. CONNECTRUNTIME MUST STAY SMALL
======================================================================

ConnectRuntime may expose convenient operations/state.

It should not absorb:

    endpoint scoring
    transport rotation
    replay cache
    TLS implementation
    session protocol
    discovery internals

Use canonical lower-level owners.

After implementation, run the consolidating-responsibilities audit again.

======================================================================
16. OUT OF SCOPE
======================================================================

Do NOT implement merely because research mentions it:

    relay infrastructure
    DERP
    STUN/TURN
    NAT hole punching
    Internet rendezvous
    streaming
    video/audio
    file transfer
    QUIC
    WireGuard
    full multipath transport

Leave clean seams where appropriate.

======================================================================
17. DOCUMENTATION
======================================================================

Update architecture docs to explain:

    stable NodeId
    root identity
    transport generations
    endpoint candidates
    logical connection/path
    logical session
    request replay/idempotency model

Include a visual such as:

    Stable NodeId
        |
        +-- root identity
        |
        +-- TLS generation N
        +-- TLS generation N+1
        |
        +-- Session
        |      +-- connection generation
        |
        +-- Endpoints
               +-- LAN IPv4
               +-- LAN IPv6
               +-- VPN

Clearly distinguish CURRENT from FUTURE functionality.

======================================================================
18. QUALITY GATES
======================================================================

Run all current project quality gates.

At minimum:

    full tests
    ruff
    ruff format --check
    mypy
    pyright
    git diff --check

Then where packaging is affected:

    build wheel
    verify wheel
    clean venv install
    import smoke
    CLI smoke

Do not claim physical multi-machine validation unless actually performed.

======================================================================
19. FINAL REPORT
======================================================================

Return:

STABLE IDENTITY / ROTATING TRANSPORT COMPLETION REPORT

BASE COMMIT:

SKILLS USED:

RESPONSIBILITY AUDIT

Identity owner:
Transport rotation owner:
Endpoint/path owner:
Connection owner:
Session owner:
Replay/idempotency owner:
Persistence owner:
ConnectRuntime changes:

IDENTITY

NodeId remains stable:
Root identity:
Transport generations:
Unauthorized rotation rejection:
Grace/overlap behavior:
Restart persistence:

ROUTES

Multi-endpoint model:
Route selection:
Failover:
IPv4/IPv6:
VPN:
All-paths-failed behavior:

SESSIONS

Logical session:
Socket replacement:
Resumption authentication:
Connection generations:

REQUEST SAFETY

Request IDs:
Duplicate handling:
Lost-response retry:
Replay bounds:
Persistence where needed:

EXTERNAL RESEARCH

Projects reviewed:
Failure scenarios reviewed:
A:
B:
C:
D:
E:

External code copied:
    MUST BE NO

TESTS

Tests before:
Tests after:
New edge-case tests:

QUALITY

Ruff:
Format:
Mypy:
Pyright:
git diff --check:

PHYSICAL VALIDATION

Loopback:
LAN:
Windows:
Linux-to-Windows:

Mark untested items honestly.

FINAL ASSESSMENT

State what is genuinely implemented now and what remains a future seam.

Do not claim "connections never drop."

The correct target is:

    stable identity
    seamless path migration where another valid route exists
    authenticated session continuity where safely resumable
    explicit offline state when no route remains
```

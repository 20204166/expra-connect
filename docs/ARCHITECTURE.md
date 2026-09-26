# Architecture

The package keeps these mechanisms distinct:

```text
Identity -> Discovery -> Pairing -> Authentication -> Authorization
         -> Connection/RPC -> Sharing
         -> Cluster membership (optional and separate)
```

Discovery finds candidates. Pairing establishes a directional relationship.
TLS and HMAC authenticate a peer. Authorization controls each request.
Connection state is Online/Offline and does not alter trust or membership.
Sharing is an explicit capability grant. Cluster role assignments are durable
topology state, not a transport state.

## Ownership Table

| Responsibility | Canonical owner | Runtime role |
| --- | --- | --- |
| Stable logical identifier | `identity.py` | `NodeId` is public and non-secret |
| Canonical device cryptographic root | `device_identity.py` | Bridges metadata in `device_identity.json` to private material in `device_identity.msgpack` |
| Network compatibility identity | `identity.py` | Bridges metadata in `identity.json` to HMAC/root private material in `identity.msgpack` |
| TLS generation lifecycle | `identity.py`, `tls_material.py` | Manages generation continuity without changing `NodeId` |
| Candidate discovery and expiry | `discovery_full.py` | Serializes backend lifecycle, normalizes per-service observations, and forwards events |
| Pairing, grants, trust, and revocation | `pairing.py` | Persists transitions and refreshes the listener ACL |
| TLS/HMAC request security | `tls_material.py`, `wire_protocol.py`, `remote_service.py` | Composes the authenticated server boundary |
| One-endpoint socket exchange | `socket_transport.py` | Connects, pins TLS before sending, frames one request/response, and closes the socket |
| Outgoing connection state and providers | `connection_manager.py`, `connection_state.py` | Connects only discovered trusted peers |
| Logical sessions and request safety | `session.py`, `provider_requests.py`, `wire_protocol.py` | Authenticates resume, fences old sockets, and bounds idempotent results |
| Target-owned capability policy | `sharing.py` | Keeps explicit, ephemeral per-peer read-only grants and injects the allowlist into `RemoteService` |
| Optional cluster membership, roles, invites, leases, and fencing | `cluster/` | Enabled only by `ConnectConfig.cluster_enabled`; flat modules are compatibility re-exports |
| Host UI/event-loop delivery | Host application | Queues callbacks onto its own UI/runtime thread |

`runtime.py` is intentionally a composition root and lifecycle facade. It does
not implement a second discovery, transport, pairing, or capability system.
`profile_state.py` owns exclusive runtime use of a profile and the paired
NodeIdentity/DeviceIdentity load-or-create lifecycle. A nonblocking OS lock is
held until runtime shutdown; a second process using the same profile fails
closed without creating a replacement identity.
`CapabilityShare` is generic target-owned sharing: registration is host-local,
grants are explicit per-peer process state, and Pair authorization remains the
outer prerequisite for every authenticated request. Pair revocation and runtime
shutdown clear generic grants while retaining host-registered handlers, so a
restart or re-pair never restores capability access without a new explicit
allow. Structured read/review/action sharing belongs to `SurfaceRegistry`.
Cluster Join is explicit and Pair-gated. Discovery and Pairing never create
membership, and connection loss never removes it.

`SocketRemoteTransport` owns one endpoint and one framed request/response
exchange. Its configured timeout is one absolute budget shared by connect, TLS
handshake, pin validation, send, and receive. Cancellation is cooperative: it
is checked at phase boundaries and polled during receive, but it does not claim
to interrupt an arbitrary DNS, OS connect, TLS, or send syscall immediately.
Route selection, fallback, reconnect, logical sessions, and transport
generation acceptance remain owned by their higher-level modules.

## Stable Identity and Transport Rotation

`NodeId` is the stable logical identifier and is not a credential.
`DeviceIdentity` is the canonical local meaning of the durable Ed25519 device
root, represented by public metadata in `device_identity.json` and private key
material in `device_identity.msgpack`; its fingerprint is derived from the raw
public key as `ed25519:<sha256>`. `NodeIdentity` remains the
compatibility/protocol identity for the HMAC secret, persisted `NodeId`, and
existing `root_public_key` and `sign_transport_proof(...)` callers. Its
non-secret metadata is in `identity.json`, while the HMAC secret and root private
key are in `identity.msgpack`. Those compatibility APIs use the same persisted
root; they do not define a second device key. Transport generation lifecycle
remains owned by `TransportGenerationManager`.

Existing device files are migrated to the authoritative root without rewriting
trust, transport, or cluster state. A successfully adopted profile records
`device_identity_expected` in `identity.json`; later loss of
either device identity file fails closed instead of generating another root. A
legacy profile without that marker may migrate once. JSON-to-MessagePack
migration writes the private bundle before atomically replacing legacy JSON with
metadata-only JSON; if interrupted before replacement, the old JSON identity
remains readable to the new release for retry.

The split profile schema is an intentional version boundary. Older JSON-only
releases cannot read migrated profiles and must not be used to downgrade those
profiles; the migration retains no secret-bearing JSON compatibility mirror.

`NodeIdentity` retains the existing HMAC-compatible secret and transport root
behavior unchanged. TLS
credentials are replaceable transport generations, identified by their pinned
certificate fingerprint. A generation proof is signed by the root and binds the
`NodeId`, generation number, and fingerprint. An advertised replacement is not
trusted merely because its `NodeId` matches.

`TransportGenerationManager` owns `current`, optional `next`, accepted overlap
generations, activation, explicit retirement, rollback, and atomic persistence.
Activation grants a bounded grace period to the previous generation. Corrupt or
unknown versioned transport state fails closed. Version-one state containing a
single fingerprint migrates to generation one; old identity JSON without an
explicit root is upgraded through the existing compatibility owner before the
canonical `DeviceIdentity` view is adopted.

`ConnectRuntime.rotate_transport()` prepares and activates a new generation,
restarts the listener, and rolls back if the replacement listener cannot start.
Discovery advertisements and authenticated hello responses carry the root key,
generation, fingerprint, and proof. A trusted peer may advance only to a
root-verified newer generation, and the accepted generation is persisted after
successful reconnect.

Logical sessions are separate from sockets. Every replacement connection still
passes TLS pinning, HMAC authentication, identity validation, and authorization;
only the session registry state is resumed. A session has an absolute TTL that
use and resume never extend, an owner bound to the authenticated identity, one
active opaque connection generation, and a permanent set of retired generations.
`assert_current` always checks that the session exists and has not expired, even
for `generation=None` (no external socket generation), and rejects any retired
or stale generation so an old in-flight handler cannot publish after a resume.
The active-session count and per-session retired-generation history are bounded;
when a bound is reached the registry fails closed and the caller must establish a
new session rather than evicting or forgetting state. The session clock defaults
to monotonic time and is separate from the wall-clock freshness clock. Mutating
requests use bounded request-ID result reuse so a lost response cannot execute
the operation twice. See `docs/SESSION_MODEL.md`.

## Persistence And Diagnostics

Identity, device identity, trust/pending pairing, transport generations, and
optional cluster membership are separate files. Legacy documents without a
schema marker are read through a fail-closed migration adapter and are written
in the current schema only after successful validation. Malformed documents are
not replaced automatically. Diagnostics report NodeId, fingerprints, transport
generations, connection states, logical session presence, and the non-secret
hardware-hint status (`match`, `changed`, `unavailable`, or `not_recorded`), but
never keys, pairing secrets, invitation tokens, raw hardware identifiers, or
fencing tokens.

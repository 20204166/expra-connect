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
| Durable node identity and fingerprint | `identity.py` | Loads identity before listener startup |
| Root signing identity and transport generations | `identity.py`, `tls_material.py` | Signs and validates credential replacement without changing `NodeId` |
| Candidate discovery and expiry | `discovery_full.py` | Serializes backend lifecycle, normalizes per-service observations, and forwards events |
| Pairing, grants, trust, and revocation | `pairing.py` | Persists transitions and refreshes the listener ACL |
| TLS/HMAC request security | `tls_material.py`, `wire_protocol.py`, `remote_service.py` | Composes the authenticated server boundary |
| Outgoing connection state and providers | `connection_manager.py`, `connection_state.py` | Connects only discovered trusted peers |
| Logical sessions and request safety | `session.py`, `provider_requests.py`, `wire_protocol.py` | Authenticates resume, fences old sockets, and bounds idempotent results |
| Target-owned capability policy | `sharing.py` | Injects the allowlist into `RemoteService` |
| Optional cluster membership, roles, invites, leases, and fencing | `cluster/` | Enabled only by `ConnectConfig.cluster_enabled`; flat modules are compatibility re-exports |
| Host UI/event-loop delivery | Host application | Queues callbacks onto its own UI/runtime thread |

`runtime.py` is intentionally a composition root and lifecycle facade. It does
not implement a second discovery, transport, pairing, or capability system.
Cluster Join is explicit and Pair-gated. Discovery and Pairing never create
membership, and connection loss never removes it.

## Stable Identity and Transport Rotation

`NodeId` is the stable logical identity. `NodeIdentity` retains the existing
HMAC-compatible secret and now also persists an Ed25519 root signing key. TLS
credentials are replaceable transport generations, identified by their pinned
certificate fingerprint. A generation proof is signed by the root and binds the
`NodeId`, generation number, and fingerprint. An advertised replacement is not
trusted merely because its `NodeId` matches.

`TransportGenerationManager` owns `current`, optional `next`, accepted overlap
generations, activation, explicit retirement, rollback, and atomic persistence.
Activation grants a bounded grace period to the previous generation. Corrupt or
unknown versioned transport state fails closed. Version-one state containing a
single fingerprint migrates to generation one; old identity JSON migrates by
generating a root key and persists it on its next save.

`ConnectRuntime.rotate_transport()` prepares and activates a new generation,
restarts the listener, and rolls back if the replacement listener cannot start.
Discovery advertisements and authenticated hello responses carry the root key,
generation, fingerprint, and proof. A trusted peer may advance only to a
root-verified newer generation, and the accepted generation is persisted after
successful reconnect.

Logical sessions are separate from sockets. Every replacement connection still
passes TLS pinning, HMAC authentication, identity validation, and authorization;
only the session registry state is resumed. Mutating requests use bounded
request-ID result reuse so a lost response cannot execute the operation twice.

## Persistence And Diagnostics

Identity, trust/pending pairing, transport generations, and optional cluster
membership are separate files. Legacy documents without a schema marker are
read through a fail-closed migration adapter and are written in the current
schema only after successful validation. Malformed documents are not replaced
automatically. Diagnostics report NodeId and fingerprints, route metadata,
transport generations, connection states, and logical session presence, but
never keys, pairing secrets, invitation tokens, or fencing tokens.

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
| Candidate discovery and expiry | `discovery_full.py` | Starts/stops backend and forwards events |
| Pairing, grants, trust, and revocation | `pairing.py` | Persists transitions and refreshes the listener ACL |
| TLS/HMAC request security | `tls_material.py`, `wire_protocol.py`, `remote_service.py` | Composes the authenticated server boundary |
| Outgoing connection state and providers | `connection_manager.py`, `connection_state.py` | Connects only discovered trusted peers |
| Target-owned capability policy | `sharing.py` | Injects the allowlist into `RemoteService` |
| Optional cluster membership and fencing | `cluster.py`, `role_engine.py` | Enabled only by `ConnectConfig.cluster_enabled` |
| Host UI/event-loop delivery | Host application | Queues callbacks onto its own UI/runtime thread |

`runtime.py` is intentionally a composition root and lifecycle facade. It does
not implement a second discovery, transport, pairing, or capability system.

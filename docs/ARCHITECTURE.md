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

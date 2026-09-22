# Security Model

Node IDs are stable identifiers, not credentials. Knowledge of a `NodeId` does
not authenticate a device. `DeviceIdentity` is the additive durable Ed25519
root bound to the existing `NodeId`; its private key is never exposed through
runtime diagnostics or sent to peers. TLS certificates are pinned
by generation fingerprint and replacement generations require a signature from
the durable Ed25519 root identity. HMAC secrets authenticate protocol envelopes. Pairing is
directional: a trusted-peer record and a peer-grant record have different
authority. A normalized relationship query must never create symmetric
permissions.

Authorization is enforced by the target for every operation. Revoke is
separate from ordinary connection detach. Private keys, HMAC secrets,
invitation material, and fencing-token values are never logged.

Cluster Join additionally requires a valid directional Pair relationship, a
targeted one-time invite, the current cluster epoch and fence, and request
freshness/idempotency. Cluster `CapabilityGrant` values are separate from
Pairing `PeerGrant` values and can narrow, but never broaden, Pair authority.

An unrelated certificate or signing root claiming a known `NodeId` is rejected.
Transport state is versioned, atomically persisted, and malformed state fails
closed. Previous generations are accepted only during the explicit bounded
activation grace period or until explicit retirement.

## Routes And Sessions

Discovery is an untrusted source of endpoint candidates. Candidates can change,
be duplicated, expire, or disappear without changing pairing or cluster state.
Reconnect creates a new physical connection through the canonical connection
owner; it does not bypass target authorization. Logical session resumption is
handled by the session owner only after the replacement transport has passed
the normal identity, freshness, and authorization checks; retired connection
generations are fenced.

## Device Identity Foundation

Phase 1 creates `device_identity.json` on first startup, including the existing
`NodeId`, raw Ed25519 key material in canonical base64, a public-key fingerprint,
creation time, and an optional digest of local hardware hints. Raw MAC addresses,
machine identifiers, and the hardware digest are local-only and are not
transmitted or used for authentication. Hardware changes never regenerate the
cryptographic identity. Existing Pair, trust, transport, and cluster state is
not migrated or rewritten.

Malformed or mismatched device identity state fails closed. A missing file on a
legacy profile is the only case that generates a new device identity.

## State Migration

State files use schema-versioned migration. Legacy one-route records are
represented as route candidates with a `legacy` source, while malformed state
fails closed and remains on disk for recovery. Migration never logs or exposes
secret-bearing fields.

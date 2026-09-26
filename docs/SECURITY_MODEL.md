# Security Model

Node IDs are stable identifiers, not credentials. Knowledge of a `NodeId` does
not authenticate a device. `DeviceIdentity` is the canonical local
representation of the durable Ed25519 device root. `NodeIdentity` retains the
compatibility/protocol state and APIs that existing peers use, including the
same root for transport proofs. Its private key is never exposed through runtime
diagnostics or sent to peers. TLS certificates are pinned
by generation fingerprint and replacement generations require a signature from
the durable Ed25519 root identity. HMAC secrets authenticate protocol envelopes. Pairing is
directional: a trusted-peer record and a peer-grant record have different
authority. A normalized relationship query must never create symmetric
permissions.

Authorization is enforced by the target for every operation. Revoke is
separate from ordinary connection detach. Private keys, HMAC secrets,
invitation material, and fencing-token values are never logged.

The socket transport validates its endpoint configuration at construction. A
certificate pin is rejected without TLS, and a `CERT_NONE` TLS context is
rejected without a pin. TLS handshake and pin verification complete before any
application envelope bytes are sent. A transport instance targets one endpoint
and one request/response exchange; route fallback, sessions, and credential
rotation remain above it. Its timeout is one total request deadline, while
cancellation is cooperative at explicit phase checks and receive polling.

CapabilityShare is a generic, target-owned, read-only router. Pairing permits an
authenticated request to reach capability authorization; it does not grant every
registered capability. A capability must be explicitly allowed for that peer,
and the target checks that grant on every request. CapabilityShare grants are
ephemeral process state: `revoke_peer()` and runtime shutdown clear them while
registered host handlers remain available. Persistence failure, revocation, and
re-pairing fail closed and never restore a previous generic grant. Capability
sharing does not authenticate peers, create trust, grant cluster authority, or
grant SurfaceRegistry access.

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

A logical session is never a credential or an authorization. It binds an
authenticated owner to one active opaque connection generation, expires on an
absolute TTL, and is validated with the same expiry rule for `generation=None`
(no physical socket generation). Retired generations stay retired permanently,
so an old socket cannot become authoritative again (no ABA). The active-session
count and per-session retired-generation history are bounded; a bound failure
invalidates the session instead of evicting a healthy one or forgetting a
retired generation. Logical sessions are process-local and ephemeral: they are
never persisted and never survive a service restart.

## Device Identity Foundation

The profile stores identity metadata in `identity.json` and the pairing secret
and root private key in the adjacent `identity.msgpack`. Device identity metadata
is in `device_identity.json`; its private key is in `device_identity.msgpack`.
All four files are profile data outside the installed Python package. They are
written atomically with owner-only permissions. MessagePack is a binary encoding,
not encryption; the sensitive bundles remain plaintext for processes running as
the profile owner. Current schemas are identity metadata v3 / secrets v1 and
device metadata v2 / private-key bundle v1.
Unrecognized extension fields are preserved in the JSON metadata document and
therefore must be non-secret; any future secret field must be explicitly added
to the MessagePack bundle schema and removed from JSON metadata.

On first startup, `device_identity.json` is created from the active
`NodeIdentity` root, including the existing `NodeId`, public-key fingerprint,
creation time, and an optional digest of local hardware hints. If a profile
contains an unrelated device root, the private key bundle is migrated to the
existing network root while its creation time and hardware metadata are
retained. Successful adoption records `device_identity_expected` in
`identity.json`. If that marker is present and either device identity file is
missing, startup fails closed. Raw MAC addresses, machine identifiers, and the
hardware digest are local-only and are not transmitted or used for
authentication. Hardware changes never regenerate the cryptographic identity.
Existing Pair, trust, transport, and cluster state is not migrated or rewritten.

Legacy full-JSON identity records are accepted and migrated to metadata JSON plus
MessagePack secret bundles. The split format uses new schema versions; older
JSON-only releases do not support profiles after migration and must fail closed
rather than regenerate a different identity. Stop older runtimes before the
first migration; the profile lock coordinates releases that implement the lock,
but cannot fence an already-running older binary.

Malformed or mismatched device identity state fails closed. A missing file is
created from the already-loaded `NodeIdentity` only when the profile has no
evidence that DeviceIdentity was previously adopted. No second network root is
generated.

Hardware evidence is non-authoritative and local-only. Diagnostics preserve the
backward-compatible `hardware_hint_changed` boolean and also report whether
evidence matched, changed, was unavailable, or was never recorded. Unavailable
evidence does not rotate the root or reject authentication.

## State Migration

State files use schema-versioned migration. Legacy one-route records are
represented as route candidates with a `legacy` source, while malformed state
fails closed and remains on disk for recovery. Migration never logs or exposes
secret-bearing fields.

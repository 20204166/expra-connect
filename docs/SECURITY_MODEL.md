# Security Model

Node IDs are stable identifiers, not credentials. TLS certificates are pinned
by fingerprint and HMAC secrets authenticate protocol envelopes. Pairing is
directional: a trusted-peer record and a peer-grant record have different
authority. A normalized relationship query must never create symmetric
permissions.

Authorization is enforced by the target for every operation. Revoke is
separate from ordinary connection detach. Private keys, HMAC secrets,
invitation material, and fencing-token values are never logged.

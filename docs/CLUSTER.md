# Expra Connect Cluster

Cluster membership is an explicit axis above authenticated Pairing and live
Connection state.

```text
DISCOVERY
    |
    | candidate only
    v
DISCOVERED PEER
    |
    | explicit Pair
    v
PAIRED / AUTHORIZED PEER
    |
    | targeted invite + explicit Join
    v
CLUSTER MEMBER
    +-- Worker
    +-- Subcoordinator
    +-- Coordinator (also Worker)

CONNECTION: Online | Offline | Migrating | Resuming
```

Discovery never joins a cluster. Pairing never joins a cluster. Disconnecting
does not remove membership. Cluster removal does not delete Pair trust unless a
host explicitly performs both operations.

## Canonical Ownership

The implementation lives under `expra_connect.cluster`:

- `models.py`: public role, assignment, epoch, lease, and invite values.
- `state.py`: immutable topology and membership state.
- `roles.py`: role transitions, grants, and active-job occupancy.
- `invites.py`: targeted, hashed, one-time invite lifecycle.
- `fencing.py`: epoch, lease, and constant-time fence validation.
- `failover.py`: promotion and returning-Coordinator decisions.
- `persistence.py`: versioned cluster-only persistence and migration.
- `operations.py`: pair-gated join and cluster mutation facade.

`role_engine.py`, `failover.py`, and the old flat `cluster.py` remain only as

## Security Intersection

Every remote cluster mutation requires:

```text
authenticated transport
    + valid directional Pair relationship
    + Pair-authorized remote permission
    + active cluster role
    + current cluster ID / epoch / fencing token
    + freshness and request idempotency
```

Cluster `CapabilityGrant` is not Pairing `PeerGrant`. A role grant can narrow
authority, never broaden the permissions already authorized by Pairing.

Fencing-token values are never included in logs, diagnostics, exception text,
or reports.

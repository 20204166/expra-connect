# Cluster Extraction Map

This map records the reusable System Analyzer cluster behavior and its single
Expra Connect owner. System Analyzer is a read-only behavioral reference; no
System Analyzer code or UI is copied.

| Source behavior | Source test/doc | Connect owner | Class | Action |
|---|---|---|---|---|
| `ClusterState.create_local` bootstrap | `tests/test_cluster_roles.py`; `REMOTE_CLUSTER_TRUE_FLOW.md` | `cluster.state` | P | Coordinator + Worker bootstrap with epoch 1 |
| Stable NodeId identity | `tests/test_membership_spec_builders.py` | `identity.py` | A | Reuse existing identity owner |
| Membership is role assignment, not trust/online | `tests/test_membership_spec_builders.py` | `cluster.state`, `registry.py` | M | Keep axes separate and hydrate registry |
| `ClusterRole` Worker/Coordinator/Subcoordinator | `maintenance/components/cluster_roles.py` | `cluster.models` | M | One canonical enum; compatibility re-export |
| Coordinator implies Worker | `test_cluster_roles.py` | `cluster.roles` | P | Normalize every assignment |
| Paused/revoked assignment invariants | `test_cluster_roles.py` | `cluster.roles` | P | Preserve transition checks |
| One active Coordinator/Subcoordinator | `test_cluster_roles.py` | `cluster.roles` | P | Validate state before publish |
| Coordinator cannot be delegated | `test_cluster_roles.py` | `cluster.roles` | P | Reject role escalation |
| Active-job occupancy | `test_cluster_roles.py` | `cluster.roles` | P | Pure metadata only; no executor |
| Scoped `CapabilityGrant` | `test_cluster_roles.py` | `cluster.roles` | P | Constrain by Pair permission intersection |
| Epoch issue/expiry and fencing token | `test_cluster_roles_persistence.py` | `cluster.fencing` | M | Canonical epoch/lease values |
| Constant-time fence validation | `test_cluster_adversarial.py` | `cluster.fencing`, remote service | A/M | Reuse `compare_digest`, never expose values |
| Exact lease boundary (`== expiry` valid renewal) | `test_cluster_roles.py` | `cluster.fencing` | P | Add deterministic regression |
| Heartbeat and durable renewal | `test_cluster_failover.py` | `cluster.failover` | P | Persist before publishing |
| One promotion per prior epoch | `test_cluster_failover.py` | `cluster.failover` | P | Atomic state transition and promotion history |
| Subcoordinator promotion/new fence | `test_cluster_failover.py` | `cluster.failover` | P | Demote former assignments to Worker |
| Returning Coordinator becomes Worker | `test_cluster_failover.py` | `cluster.failover` | P | Clear pause/job, reject stale epoch |
| One-time invite and target binding | `test_cluster_roles_persistence.py` | `cluster.invites` | P | Hash token at rest and bind target |
| Invite cluster/epoch/fence/expiry binding | `test_cluster_roles_persistence.py` | `cluster.invites` | P | Validate before consume |
| Invite blob encode/decode | `test_cluster_roles_persistence.py` | `cluster.invites` | P | Headless base64 JSON boundary |
| Invite replay/expiry/malformed rejection | `test_cluster_roles_persistence.py` | `cluster.invites` | P | Fail closed |
| Active invite restart behavior | `test_cluster_roles_persistence.py` | `cluster.persistence` | C | Persist hashed active records |
| Atomic save and save-before-publish | `test_cluster.py`, patch review 20260915-007 | `cluster.persistence`, operations | M | Replacement-state transaction |
| Existing Connect cluster migration | current `cluster.py` schema | `cluster.persistence` | M | Preserve cluster ID, roles, epoch, used invites |
| Pairing trust records | `SECURITY.md`; remote security tests | `pairing.py` | A | Do not copy into cluster |
| Directional Pair relationship admission | `test_remote_security.py` | `pairing.py`, `cluster.operations` | P | Add `can_join_cluster` owner API |
| Pair does not imply Join | `test_cluster_page.py`, join plan | `cluster.operations` | P | Explicit pair-gated join only |
| Authenticated remote Join | join plan; `REMOTE_CLUSTER_TRUE_FLOW.md` | `cluster.operations`, `runtime.py` | P | Use existing RemoteService path |
| Join response/idempotency/retry | join plan; remote contract tests | wire/runtime/cluster | P | Request ID and durable result semantics |
| Role RPC fence and Pair checks | `test_remote_contract.py` | `remote_service.py`, runtime | M | Complete operation table and dispatch |
| Worker snapshot seam | `test_remote_contract.py` | `cluster.operations` | X | Generic host snapshot only; no scanner model |
| Standby/timeline storage | `test_cluster_storage.py` | none | X | Hardware/application history outside Connect scope |
| `remove_connection` meaning | `test_remote_compatibility.py` | `runtime` connection owner | S | Clear socket only, never membership/trust |
| `remove_job` | `test_remote_compatibility.py` | `cluster.roles` | P | Clear occupancy only |
| Tk pages/dialogs | UI cluster tests | none | X | Headless boundary |
| Placement/resource telemetry | placement/storage tests | none | X | Future host concern |
| Discovery/route/rotation lifecycle | Connect discovery parity tests | discovery, connection, cluster | A | Preserve membership across transport changes |

## Trust Boundary

System Analyzer historically placed trust and topology in one persisted object.
Connect intentionally does not. `PairingManager` owns `TrustedPeer`,
`PeerGrant`, secrets, and directional relationship state. The cluster package
owns topology, roles, invites, epoch, lease, and fencing. Join requires both a
valid Pair relationship and a valid cluster invite; neither axis implies the
other.

## Disposition Totals

The map contains 38 meaningful behavior rows: P 19, A 4, M 8, S 1, U 0, X 6.
Rows marked X are intentionally excluded with a reason above; no reusable
source behavior is unexplained.

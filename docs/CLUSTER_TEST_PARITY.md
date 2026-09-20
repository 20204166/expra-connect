# Cluster Test Parity

The matrix tests invariants through the canonical Connect API rather than
copying System Analyzer private helpers.

| Source test/invariant | Connect equivalent | Status |
|---|---|---|
| `test_coordinator_always_has_worker_role` | `tests/test_cluster_roles.py` | C |
| `test_worker_cannot_assign_roles` | `tests/test_role_engine.py` | A |
| `test_only_one_subcoordinator` | `tests/test_role_engine.py` | A |
| `test_two_active_coordinators_are_rejected` | `tests/test_cluster_persistence.py` | A |
| `test_promotion_increments_epoch_after_timeout` | `tests/test_role_engine.py` | A |
| `test_promotion_before_timeout_is_rejected` | `tests/test_role_engine.py` | A |
| `test_stale_lease_token_is_rejected` | `tests/test_role_engine.py` | A |
| `test_non_finite_epoch_times_are_rejected` | `tests/test_cluster_roles_persistence.py` | C |
| `test_returning_coordinator_is_fenced_to_worker` | `tests/test_role_engine.py` | A |
| active-job assign/remove/revoke semantics | `tests/test_role_engine.py` | B |
| scoped capability grant/expiry/revocation | `tests/test_role_engine.py` | A |
| duplicate role normalization on restart | `tests/test_cluster_roles_persistence.py` | B |
| active jobs normalize false on restart | `tests/test_role_engine.py` | A |
| invite binds target and current fence | `tests/test_cluster.py` | B |
| invite hash is persisted, raw token is not | new `tests/test_cluster_invites.py` | C |
| invite survives restart | new `tests/test_cluster_invites.py` | C |
| invite replay/expiry/wrong target | `tests/test_cluster.py` | B |
| malformed persistence fails closed | `tests/test_cluster_persistence.py` | B |
| save failure does not publish mutation | new cluster transaction tests | C |
| paired peer is not automatically a member | new pairing/cluster tests | C |
| unpaired Join rejected without mutation | new pairing/cluster tests | C |
| valid Pair + invite Join succeeds | new runtime join tests | C |
| Pair revoke blocks later cluster mutation | new runtime join tests | C |
| Join does not create reverse TrustedPeer | new directional pairing tests | C |
| offline preserves membership | `tests/test_cluster.py` | A |
| route change preserves membership | discovery/runtime parity tests | B |
| valid transport rotation preserves membership | runtime transport tests | B |
| stale epoch/fence rejects role RPC | `tests/test_runtime.py` | B |
| role RPC operation-table completeness | new remote role tests | C |
| role RPC request-id retry is idempotent | new remote role tests | C |
| heartbeat exact-expiry semantics | new failover tests | C |
| concurrent promotion has one winner | new failover tests | C |
| old Coordinator cannot mutate after promotion | new failover tests | C |
| revoked node needs fresh admission | `tests/test_role_engine.py` | A |
| `remove_connection` changes connection only | new runtime tests | C |
| `remove_job` changes occupancy only | new role tests | B |
| worker snapshot is host-supplied | new remote role tests | D |
| standby/timeline retention | none | E |
| Tk/UI projection | none | D |

## Status Meaning

- A: already covered.
- B: implementation owner exists but equivalent regression coverage is missing.
- C: implementation or integration gap requiring code and test work.
- D: UI/physical/application-specific and not applicable to headless Connect.
- E: superseded or intentionally excluded with a documented reason.

No source cluster test is left without a classification.

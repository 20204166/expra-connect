# Edge-Case Research

Research was performed on 2026-09-20. External material was used to identify
failure cases, not to copy implementations. The local System Analyzer reference
requested by the repository guide was unavailable at `/home/btn17/Downloads/exp`,
so no parity claim is made here.

## Sources And Findings

| Source | Failure case | Class | Expra treatment |
| --- | --- | --- | --- |
| [Syncthing FAQ, device IDs](https://docs.syncthing.net/users/faq.html#should-i-keep-my-device-ids-secret) | Discovery of an identifier does not grant connection or data access. | A | NodeId, discovery, pairing, and authorization stay separate. |
| [Syncthing FAQ, database corruption](https://docs.syncthing.net/users/faq.html#my-syncthing-database-is-corrupt) | Corrupt durable state must stop safely rather than be silently replaced. | B | `JsonStateStore` raises `StateDataError`; malformed files remain untouched. |
| [Syncthing issue #8828](https://github.com/syncthing/syncthing/issues/8828) | Timing-sensitive persistence/database tests can be flaky. | D | Atomic writes and lifecycle generation fencing are the current seams. |
| [Tailscale connection types](https://tailscale.com/kb/1257/connection-types) | A route can change type or disappear while the logical peer remains. | B | Candidates are keyed by NodeId and reconnect delegates to the connection owner. |
| [Tailscale direct fallback](https://tailscale.com/kb/1257/connection-types#how-tailscale-establishes-connections) | Prefer a direct candidate while retaining fallback and reporting path state. | B | `ConnectionManager` owns deterministic candidate ranking, fallback, migration state, and offline reporting. |
| [libp2p security overview](https://docs.libp2p.io/concepts/fundamentals/security/security-overview/) | The official page returned HTTP 401 during this run. | E | No design decision or code was based on this unavailable source. |

## Failure Matrix

| Area | Scenarios | Class | Decision |
| --- | --- | --- | --- |
| Identity/rotation | Offline/active rotation, restart, retirement, rollback, races, corruption, unrelated fingerprint | B/C | Signed generations, durable rollback, bounded grace, and fail-closed decode belong to `TransportGenerationManager`; runtime initializes and exposes it. |
| Routes | IP change, IPv4/IPv6, VPN, stale/duplicate discovery, conflicting endpoints, path failure | B/C | Route candidates remain below stable identity; `ConnectionManager` performs authenticated fallback and generation-fenced reconnect. |
| Connection | Simultaneous starts, duplicate attempts, vanished route, replacement authentication, stale callback | B/C | Runtime fences lifecycle callbacks; connection retry and route selection remain in `ConnectionManager`. |
| Session | Dead socket, resume, wrong identity, expiry, duplicate resume, old-generation message | B | `LogicalSessionRegistry` authenticates resume and fences retired connection generations. |
| Requests | Lost mutation response, retry, concurrent duplicate, restart durability, malformed ID, revoked permission | A/B | `IdempotencyCache` provides bounded concurrent result reuse and optional durable result persistence. |
| Lifecycle | Shutdown during migration/rotation, restart reconnect, stale callbacks, persistence failure | B | Reverse-order shutdown, generation checks, atomic stores, and rollback-on-persistence-failure are covered. |

No external code was copied. Relay, DERP, STUN/TURN, QUIC, streaming, and
multipath transport remain out of scope.

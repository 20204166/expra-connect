# Discovery Edge-Case Parity

Audit date: 2026-09-20

System Analyzer code copied: **NO**
System Analyzer edge-case knowledge adapted: **YES**

This matrix records failure invariants from the read-only System Analyzer
reference and their owner in Expra Connect. The reference tree is not a Git
checkout, so commit identifiers below are the historical identifiers recorded
in its review documents; no unrecorded history is claimed.

## Matrix

| Source test / bug / commit | Failure scenario and expected behavior | Expra Connect owner | Existing Connect test | Status | Action |
| --- | --- | --- | --- | --- | --- |
| `test_network_discovery.py:237-264` | Repeated start/stop is harmless; unavailable Zeroconf keeps the app local-only. | `NetworkDiscovery`, runtime | `test_discovery_full.py`; `test_runtime.py` | A | none |
| `test_network_discovery.py:265-283` | Partial backend startup is cleaned up and discovery stays inactive. | `NetworkDiscovery.start` | `test_runtime.py` backend-failure cases | A | none |
| `test_network_discovery.py:406-466`; PATCH-010/011/012 | Start and stop transactions serialize, including blocked backend start and stop. | `NetworkDiscovery` lifecycle lock | none | B | add regression test |
| PATCH-012:172-215 | A synchronous backend callback during startup must not be silently lost. | `NetworkDiscovery` lifecycle state | none | C | strengthen implementation and test |
| `test_network_discovery.py:524-531`; coordinator tests | Late callback after stop cannot mutate candidates. | `NetworkDiscovery`, runtime generation | runtime stale-callback test; no direct full-discovery race test | B | add regression test |
| `test_coordinator_discovery.py:84-100` | Failed start releases ownership so a later start can retry. | runtime composition | backend failure tests | A | none |
| `discovery_session.py:192-201`; session tests | Stop cancels expiry/stabilization work and restart is possible. | runtime expiry worker | runtime restart tests | E | document only |
| PATCH-20260906-002; `test_network_discovery.py:103-235` | Zeroconf without optional `addresses` API still starts; all-interface startup falls back to IPv4. | `ZeroconfDiscoveryBackend` | none | B | add regression test |
| `test_network_discovery.py:474-496` | Bytes TXT values and parsed IPv6 addresses normalize. | backend adapter / normalization | bytes properties not covered; IPv6 ranking only | B | add regression test |
| reference backend / Windows findings | Empty or unavailable address collections do not crash; IPv4-only, IPv6-only, and mixed records remain representable. | normalization | IPv6 route test only | B | add regression test |
| `test_network_discovery.py:468-472`, malformed-record cases | Missing TXT identity, malformed service name, missing service info, and unknown removal are ignored. | `_normalize`, listener | missing ID and custom service tests | B | add regression test |
| `test_network_discovery.py:329-351` | Exact local NodeId is filtered; local hostname/address with another NodeId is not self. | stable-ID self filter | exact self only | B | add regression test |
| PATCH-20260908-003 | Literal/empty identity is not accepted as a peer; identity persistence prevents collisions. | identity and normalization | legacy `local` only | B | add regression test |
| `test_network_discovery.py:353-376` | Same NodeId deduplicates; latest valid hostname/address observation updates metadata. | `NetworkDiscovery` candidate lifecycle | duplicate merge test; update test | A | none |
| reference multi-homing tests | Duplicate addresses are unique; separate service names for one NodeId retain usable routes; removing one route does not remove another. | `NetworkDiscovery` service-to-peer map | partial duplicate-service coverage | B | add regression test |
| reference malformed-port tests; PATCH-011/012 | Boolean, fractional, string, negative, and out-of-range ports become non-connectable rather than raising or truncating. Port `0` is not a destination. | normalization / `EndpointCandidate` boundary | legacy port helper only | C | strengthen implementation and test |
| reference out-of-order event tests | Unknown remove is harmless; update before add and reappearance do not corrupt state; latest valid observation wins. | `NetworkDiscovery` | no direct coverage | B | add regression test |
| `test_network_discovery.py:541-593` | Remove is immediate; expiry is strictly after TTL; observation refreshes TTL without duplicate event. | `NetworkDiscovery.expire_stale` | custom TTL boundary only | B | add regression test |
| Phase 12I; Windows findings 1547-1599 | Application TTL must exceed mDNS record TTL; no update callback for routine reannounce must not evict a live peer. | `DEFAULT_TTL_SECONDS` | default constant not tested against mDNS TTL | B | add regression test |
| reference TTL tests | Expiry does not erase trust, membership, or durable identity; connected peers become offline/retry candidates only. | runtime, registry, connection owner | trust separation exists; no expiry integration | B | add regression test |
| SEC-20260912-002; network callback tests | Two callbacks, expiry, and shutdown serialize without contradictory candidate state. | `NetworkDiscovery._peer_lock` | no deterministic concurrent full-discovery test | B | add regression test |
| coordinator generation tests | Callback queued before stop/restart cannot commit to the new lifecycle. | runtime `_generation` | `test_runtime.py` stale callback coverage | A | none |
| `REMOTE_CLUSTER_TRUE_FLOW.md:270-286`; E2E tests | Backend event flows through normalized candidate, registry, routes, and connection reconciliation without granting trust. | backend, runtime, registry, `ConnectionManager` | registry/security and runtime tests | B | add pipeline regression test |
| identity continuity tests | Hostname/address changes do not change stable NodeId; same hostname with different NodeIds stays separate. | discovery stable ID; registry | same-host discovery not covered | B | add regression test |
| reference fingerprint tests | Discovery fingerprint is metadata only; unrelated root or forged transport fingerprint is rejected at authenticated connection. | `ConnectionManager`, identity | wrong fingerprint rediscovery filter | E | document only |
| signed-generation adaptation | Changed transport fingerprint without signed continuity is rejected; valid newer signed generation is allowed and persisted. | runtime validation, `ConnectionManager` | connection generation tests exist; discovery branch absent | C | strengthen implementation and test |
| trusted rediscovery tests | Address changes preserve trust; route metadata changes independently; undiscovery does not revoke trust or membership. | runtime, registry, connection manager | trust separation exists; no full rediscovery test | B | add regression test |
| reference pairing disappearance cases | Candidate loss/change during pairing does not corrupt pairing; failed pairing leaves candidate available; route failover remains possible. | `NetworkPairing`, runtime candidate map | cancellation/rollback and failover tests | E | document only |
| `test_failover.py` and connection history | Preferred route failure tries the next route; all routes fail offline; stale attempt cannot replace newer success. | `ConnectionManager`, connection state | route failover coverage exists | A | none |
| peer connection tests | Discovery loss marks connection offline and schedules bounded retry; authentication failure does not retry; manual disconnect suppresses rediscovery reconnect. | `ConnectionManager`, `connection_state` | failure classification and disconnect coverage | B | add regression test |
| Windows listener findings; runtime advertisement test | Advertisement uses actual bound port, NodeId, version, current fingerprint/generation, and connectability. | runtime/server/backend | actual port and fingerprint test | A | none |
| Windows/VPN findings | Multiple interfaces and VPN routes remain candidates; route scoring, not discovery, chooses the route. | `ConnectionManager` and endpoint ranking | VPN-only/preference tests | B | add regression test |
| SEC-20260912-002 | Advertisement cannot trust, pair, authorize, join cluster, accept keys, override root identity, or expose secrets. | runtime, registry, pairing, wire security | discovery trust boundary and diagnostics tests | A | none |
| `WINDOWS-FINDINGS-2026-09-17.md` | Windows mDNS/firewall/VPN/interface behavior is physical evidence, not proven by fake backend tests. | acceptance documentation | no physical run in this audit | D | document only |
| `WINDOWS-FINDINGS-2026-09-17.md:1600-1607` | Controlled post-TTL physical continuity observation remained unperformed in the source history. | acceptance process | not run | D | document only |

## Classification Summary Before Changes

The rows marked **C** are confirmed implementation risks in the current
Connect code: startup callback loss, port zero escaping normalization, and
valid signed transport-generation announcements being rejected before
`ConnectionManager`. The rows marked **B** have an owner and mostly-correct
behavior but need deterministic regression coverage. **E** rows are cases
whose old mechanism is superseded by Connect's signed generations, route
candidate model, logical sessions, or headless lifecycle. **D** rows are
physical or UI/application-specific observations that are documented but not
claimed as automated Connect behavior.

## Physical Validation Boundary

No Windows, LAN, firewall, VPN, or real mDNS validation was run for this
audit. Fake backend tests can prove normalization and lifecycle invariants
only; they cannot prove interface binding, firewall behavior, or cross-host
reachability.

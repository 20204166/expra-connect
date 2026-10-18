# Cluster-state round-trip performance design

## Goal

Simplify and improve the performance of the existing cluster-state and role-state codecs without changing observable serialization, migration, validation, or persistence behavior.

## Ownership and scope

`src/expra_connect/cluster/state.py` remains the canonical owner of strict role-state encoding and decoding. `ClusterState.to_dict` and `ClusterState.from_dict` in `operations.py` remain responsible for the schema-v2 aggregate, legacy aliases, invite records, and used-invite normalization. The mature `exp` implementation does not provide a matching strict codec to transplant.

The current checkout includes `bee000a` (`perf: avoid redundant role enum conversion`), which avoids reconstructing values that are already `ClusterRole` instances in `RoleAssignment.__post_init__`. Treat that as part of the current benchmark baseline; do not attribute its effect to a later codec change.

Only the codec paths and their directly relevant tests are in scope. No new generic serialization layer or public API is proposed. Any change to model construction is in scope only if a profile demonstrates it is still a meaningful cost after `bee000a`.

## Measurement

Measure dict encode/decode and end-to-end round-trip at 1,000, 30,000, and 100,000 assignments/grants. Also measure YAML encode, parse, and full in-memory YAML-to-`ClusterState` round-trip through 30,000 entries; the 100,000-entry YAML case is excluded because the 1,000-entry smoke run showed that PyYAML dominates runtime. Keep fixture shape equivalent across sizes and report JSON and YAML payload sizes. YAML encode/decode must use the same `yaml.safe_dump(..., sort_keys=True, allow_unicode=True)` and `yaml.safe_load` behavior as `YamlStateStore`; exclude filesystem and `fsync` costs from this in-memory serializer comparison. Use warmups and repeated samples; report median and range with interpreter/environment. Compare the same current source, fixture, and interpreter before and after. The native-wheel baseline's 30k/100k results are historical context only: its sources differ, and its measurements do not include allocation or split encode/decode. Do not claim improvement from cross-release comparisons. Keep a change only when repeated same-source measurements show a useful latency or allocation improvement without a material regression at another size.

## Behavioral requirements

- Preserve the exact serialized fields and ordering conventions, including the aggregate's legacy `role` alias.
- Preserve schema-v2 defaulting and `role_assignments` / `local_id` compatibility.
- Preserve strict input type and finite-number validation, role invariants, invite validation, and legacy used-token hashing.
- Preserve runtime-only job-state normalization and all persisted invite secret boundaries.
- Do not alter unrelated cluster runtime behavior.

## Test and verification plan

Extend existing codec/persistence tests before implementation to pin complete role-state and cluster-state round trips and compatibility cases. Include legacy aliases, runtime-only job-state normalization, invite/used-token handling, and exact serialized shape in the assertions. Run `tests/test_cluster.py`, `tests/test_cluster_persistence.py`, `tests/test_role_engine.py`, and `tests/test_role_hardening.py`; then run the repository unittest, Ruff, Pyright, and Mypy checks required by `AGENTS.md`. Re-run the identical benchmark after each retained optimization and inspect the final diff.

## Alternatives considered

1. **Keep current implementation:** lowest risk; appropriate if profiling shows no material codec-owned cost.
2. **Optimize locally within the two canonical codecs (recommended conditionally):** retains ownership and contracts while permitting measured allocation/work reductions; proceed only with profile and benchmark evidence.
3. **Create a shared generic codec or port the mature implementation:** rejected because it adds an abstraction without a real common contract; the mature implementation does not implement this strict codec's behavior.

# Persistence simplification and native build design

## Goal

Make a conservative, behavior-preserving simplification in `expra_connect.persistence` and include that module in the native Cython wheel. Do not run performance benchmarks or claim that the reported 30k timings improve.

## Boundaries and compatibility

Keep public store APIs, schema migration semantics, deterministic JSON/YAML encoding, MessagePack validation and size limits, error translation, private file permissions, atomic replacement, file `fsync`, and best-effort directory `fsync` unchanged. Preserve Python source files and the package surface in wheels. Do not change dependencies or unrelated persistence callers.

The implementation change is limited to a small simplification supported by existing behavior/tests; it must not weaken validation or durability to chase timings. Add `expra_connect.persistence` to the canonical native module selection and wheel verification so compiled-wheel expectations agree.

## Validation

Add or adapt regression assertions for any changed behavior before implementation. Run focused persistence/native-build tests, then repository-required unittest, Ruff, Pyright, and Mypy checks, plus the native build/wheel verifier if practical. Do not run any benchmark harness. Report verification results separately from performance: no speedup is established until a future benchmark run.

## Non-goals

- No changes to the eight 30k benchmark fixtures or recorded results.
- No benchmark execution or performance claim.
- No change to storage format, ordering, atomic-write sequence, or durability policy.
- No broad refactor of persistence callers or adjacent modules.

# Recent-Baseline Comparison Design

## Goal

Make recent-reference performance visible across the comparison outputs without
conflating it with the existing historical comparison against `1.8.0.0`.

## Display contract

- Preserve the existing historical charts, scores, and weighted winner normalized
  against `1.8.0.0`.
- Add a full recent-reference comparison normalized against `2.2.1.0`, using
  native as the native reference and universal as the universal reference.
- Use only comparable matched positive-p50 cases and existing score semantics;
  do not alter raw measurements or benchmark result data.
- Label every recent score, axis, legend, and explanation with its `2.2.1.0`
  denominator and flavor-specific baseline.
- Keep the recent comparison separate from historical winner claims.

## Validation

Test both denominator paths, confirm `2.2.1.0` scores are `100.000p` for each
flavor, regenerate comparison artifacts, and verify benchmark JSON and release
artifacts remain unchanged.

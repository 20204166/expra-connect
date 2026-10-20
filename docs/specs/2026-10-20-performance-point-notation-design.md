# Performance Point Notation Design

## Goal

Replace percentage notation for normalized performance scores with a custom
`p`-suffixed point scale. The historical baseline remains 100 points, and
smaller scores indicate faster performance.

## Display contract

- Convert every normalized ratio to points by multiplying by 100.
- Render scores with three decimal places and a `p` suffix: `100.000p` is the
  baseline, `80.000p` is faster, and `134.000p` is slower.
- Keep the historical anchor and recent-reference denominators distinct.
- Use point notation for normalized score values, scale ticks, score legends,
  and score explanations in generated charts and summaries.
- Keep literal workload/category weights (for example, `5%`) as percentages.
- Preserve raw benchmark measurements, scoring calculations, wheel artifacts,
  and benchmark result JSON unchanged.

## Scope and validation

Update the performance diagram generator, its tests, and generated outputs.
Verify score formatting and denominator explanations, generated-output
freshness, the full test suite, Ruff, Pyright, and Mypy. Check that benchmark
measurements and wheel checksums remain unchanged.

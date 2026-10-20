# UI audit — latest comparisons

```text
task_type: visual polish + consolidation of two generated comparison sections
stack: generated SVG from Python renderer; Markdown embedding
surface_size: one dense benchmark matrix
evidence_availability: renderable, static SVG
mode: visual polish
risk: low visual risk; no score calculation change
```

## Finding

The latest per-case matrix and recent-reference score table were separate SVGs with different layouts. This split made the baseline scores harder to compare alongside the case-level results. The existing latest-build matrix is the canonical visual owner and already contains the latest-vs-reference and latest cross-flavor pairings.

## Accepted scope

Keep one latest-comparisons matrix, place weighted/full-workload recent scores in the matching-flavor column headers, retain the per-case rows and cross-flavor columns, and remove the standalone recent-reference SVG. Keep historical `1.8.0.0` charts and winner unchanged.

## Design constraints

Use `.ui/latest-comparisons/design-language.md`; do not add visual language or change benchmark calculations.

# UI Change Plan — Performance comparison

## Follow-up spacing fix — 2026-10-19

- **Mode/risk:** visual polish / low.
- **Accepted issue:** UI-004 — keep all final point-panel content within the SVG
  viewBox so the Markdown image does not clip the bottom of the diagram.
- **Patch:** derive trailing height from actual rendered sections using shared
  spacing constants; retain the existing chart design and data ordering.
- **Rollback:** revert the trailing-height calculation/constants and the
  corresponding viewBox-bound regression test.
- **Opposition decision:** accepted after the independent review's request to
  share spacing values and provide viewport renders was addressed.
- **Validation:** chart unit tests, SVG parsing, deterministic generator check,
  and Firefox headless renders at 375, 768, and 1,385 CSS px.

## Proposed plan (Stage 8)

- Surface slug: `performance-comparison`
- Mode: audit only (user requested audit before implementation)
- Risk: medium
- Plan date: 2026-10-18

### Triage bands

| Band | Issue ids | Rationale |
|---|---|---|
| must fix | UI-001 | Avoid misleading a connected line as same-workload scaling |
| should fix | UI-002 | Qualify per-size lowest observed index and expose case coverage |
| optional polish | — | No other visual change justified |
| defer | UI-003 | Narrow-viewport and assistive-technology validation until a chart patch exists |
| do not touch | — | Preserve current heatmap and user-owned source changes |

### Proposed changes

```text
UI-001
proposed_change: Append four per-size point panels using matched p50 latency ratios; do not connect points across sizes.
issue_supporting: UI-001
proposed_files: scripts/update_performance_diagrams.py, tests/test_performance_diagrams.py, docs/images/performance-comparison.svg
token_or_variant_reused: existing SVG palette, typography, and ratio direction
justification_if_new: n/a
estimated_regression_risk: medium
validation_required: render at 375/768/1385 CSS px; verify SVG XML and full chart readability; existing and new panels deterministic
opposition_required: yes — per-size suite mix can mislead comparisons
```

```text
UI-002
proposed_change: Show suite/case counts and state “lowest observed index in this panel,” not “best version.”
issue_supporting: UI-002
proposed_files: scripts/update_performance_diagrams.py, docs/native-wheel-performance-baseline.md
token_or_variant_reused: existing chart labels and caveat copy style
justification_if_new: n/a
estimated_regression_risk: low
validation_required: inspect copy and SVG title/aria label; assert legend, N/A and caveat labels
opposition_required: yes — winner wording is a semantic claim
```

### UI budget at proposal

```text
max new hardcoded colours: 0
max new arbitrary spacing values: 0
max new !important: 0
max specificity increase: none
max new global selectors: 0
max new dependencies: 0
min viewport coverage: 3 (375 / 768 / 1385 CSS px)
min state coverage: static chart + unavailable/unsupported data labels
```

## Synthesised plan (Stage 10)

| Change | Decision | Critic | Modification |
|---|---|---|---|
| UI-001 | modified; pending user acceptance | independent opposition reviewer | Replace connected lines with four per-size point panels/dot matrix; same shared index scale; no cross-size trend line. |
| UI-002 | modified; pending user acceptance | independent opposition reviewer | Use “lowest observed index in this panel,” disclose suite and common-case count, and state that scores are descriptive p50 summaries. |

### Accepted implementation order

Implementation is paused. Once the user accepts the modified format:

1. Test-first: implement synthetic coverage/score tests and exact case-selection tests.
2. Add per-size points in the canonical updater; leave existing panels/data untouched.
3. Regenerate one SVG, update methodology text, then validate three viewports and accessibility labeling.

### Rollback notes

- Keep the existing single-SVG chart and revert only the appended panel/scorer/test/docs changes if validation fails; raw JSON is read-only and unchanged.

### BugGuard handoffs

| Issue | Reason | Evidence |
|---|---|---|
| None | The proposed change is a descriptive docs visualization, not runtime benchmark behavior. Score semantics remain covered by explicit synthetic tests. | audit `F-001`, `F-002` |

### Validation gates to be exercised

- Static: deterministic generation, XML parsing, correct common-case set and formula tests.
- Render: 375 / 768 / 1385 CSS px with all four panels.
- Accessibility: SVG title/name; inspect text/legend and accessible summary. No controls/keyboard flow are introduced.
- CSS architecture: not applicable; SVG is generated without CSS stylesheets.

## Anti-generic-AI certification

> The proposal reuses the existing chart palette, typography, labels, and density; it introduces no new dependencies or palette decisions. Opposition modified the line graph into separate point panels to preserve the data’s actual comparability.

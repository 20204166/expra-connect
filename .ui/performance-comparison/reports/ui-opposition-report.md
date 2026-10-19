# Opposition report — performance size index

## Follow-up spacing patch opposition — 2026-10-19

- **Independent reviewer decision:** modify before accepting; share the layout
  offsets between measurement and rendering, and provide actual viewport
  screenshots before claiming no overlap/overflow.
- **Resolution:** accepted after shared spacing constants were used and Firefox
  headless renders at 375, 768, and 1,385 CSS px were captured and inspected.
- **Evidence:** previous full chart render exposed content baseline y=4,857
  outside the 4,836px viewBox; updated generated SVG includes the last labels
  with bottom padding. Regression test asserts text and circle geometry fits.
- **Remaining limitation:** responsive scaling preserves all content but makes
  text smaller on narrow widths; the single-SVG format is intentionally kept.

## Reviewer

Independent read-only opposition review (general reviewer agent), scoped to the
size-specific score semantics, visual encoding, and “best” wording.

## Findings

1. **High: a connected line implies a same-workload scaling curve that is not
   present in the data.** At 1k the score uses nine cluster-state cases; at
   30k/100k it uses six cluster-large cases; at 200k it switches to six JSON
   persistence cases. Connecting points can misrepresent a suite change as
   scaling behavior.
2. **High: “best at each size” can overstate the evidence.** The lowest score
   comes from a different case mix at each size and is a descriptive median
   summary, not a universal release winner.
3. **Medium: a geometric mean is not uncertainty-aware.** It can make small
   differences look definitive despite run variability, differing cluster
   configurations, and sequential native/universal measurement order.

## Safer alternative

Use four per-size point panels (or a dot matrix) with a shared index scale and
all nine entries. Put the suite and common-case count in each heading; mark the
panel minimum “lowest observed index,” not “best.” Do not connect points. State
that each size uses its own matched available workload mix and that values are
descriptive p50 ratios, not statistical significance or causal release effects.

## Resolution

The connected-line mockup is **modified**: preserve its requested per-version
point comparison, but remove connecting lines and replace the “best at size”
claim with a per-panel lowest-observed label. User review is required before
implementation because this differs from the approved mockup’s connected
lines.

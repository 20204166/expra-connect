# UI Opposition Review — Latest Comparisons Matrix

## Evidence and scope

- Before: `/tmp/opencode/latest-build-before.png`
- After: `/tmp/opencode/latest-comparisons-after.png`
- Review is limited to the rendered SVG screenshots at the available viewport/scale. No source, DOM, CSS, interaction, or accessibility-tree inspection was performed; conclusions below are visual-only.

## Findings

### Accepted — consolidated comparison matrix

The after view presents the four native/universal candidate-reference pairings in one continuous matrix under “Latest comparisons,” rather than splitting recent-baseline content into a separate latest section. The four columns remain aligned to the same case rows and grouped sections (“Cluster State,” “Persistence,” and “Connect”). Pale-blue header/section bands, alternating row backgrounds, and blue/orange/red score cells retain the visual language visible in the before screenshot. The added title/context and comparison notes provide a useful hierarchy without disrupting the table structure.

### Reduced concern — weighted/full-workload header legibility; remaining dense cell text (Low; visual confidence: Medium)

Re-reviewing the regenerated after view against the before view, the weighted/full-workload header labels are now visibly bold and more distinct beneath the candidate/reference labels. This reduces the prior header-legibility concern; the adjustment improves hierarchy without changing the four-column structure. It does not fully resolve the broader small-text issue: at the screenshot’s rendered scale, long case labels and candidate/reference score strings in the matrix remain very small and difficult to scan. The dense case-level cells are the remaining concrete legibility concern. If close reading at this display size is required, consider a narrowly scoped adjustment while retaining the established colors and matrix structure.

## Review dimensions

- **Visual consistency — accepted.** The after matrix retains the before view’s restrained pale-blue/gray table treatment and score-color semantics. No conspicuous new visual language is introduced.
- **Header adjustment — accepted, concern reduced.** Relative to `/tmp/opencode/latest-build-before.png`, the weighted/full-workload details in the first and third headers are now visibly bold (reported as 11px) and easier to pick out in `/tmp/opencode/latest-comparisons-after.png`. The change improves header hierarchy; case-level cell text remains dense and small.
- **Accessibility — limited / not verified.** The screenshot shows small text and score meaning that appears to rely partly on color. This supports a legibility concern, but does not establish contrast ratios, semantic table/header associations, non-color cues, or assistive-technology behavior. Those are not assessable from these screenshots alone.
- **CSS/rendering architecture — N/A.** This is an exported SVG comparison; no CSS/source/cascade evidence was provided. No architecture judgment is made.
- **Responsive/layout — limited / not verified.** The single screenshot shows the four columns fitting the captured canvas and rows staying aligned. No alternate viewport, scaling, clipping, or scroll behavior evidence is available, so responsive behavior cannot be assessed.
- **Scope/regression — accepted within visible scope.** The requested consolidation is visible and the section groups, row ordering, four pairings, and score-color treatment appear retained. Data correctness and other states are not verified by visual comparison.
- **Smaller-fix review.** Avoid broad restyling or structural changes. If readability at the intended display size is a requirement, prefer a narrowly scoped text-size/spacing or header-detail treatment and re-check the rendered matrix; do not infer a specific CSS fix from the SVG alone.
- **SVG-specific N/A.** DOM-based keyboard/focus behavior, live responsive reflow, motion/reduced-motion, and browser CSS behavior cannot be evaluated from static SVG screenshots.

## Safe disposition

Accept the single-matrix consolidation and the weighted/full-workload header emphasis as visually successful; the emphasis reduces the header concern. Preserve the existing table language. Retain the case-level text density as a follow-up legibility concern against the intended display size; do not make production changes based solely on this screenshot review. Validate responsive and assistive-technology properties separately if they are in scope.

## Addendum — score-point emphasis and p50 tooltips

Compared `/tmp/opencode/latest-comparisons-improved.png` with both earlier screenshots. Promoting candidate/reference score points to bold 12px cell labels concretely improves scanning: the leading value is now easy to locate within each populated score cell, unlike the long, tiny inline candidate/reference strings in the prior after/before views. It also makes values scannable without relying solely on the blue/orange heatmap. The updated render keeps the same row layout, colors, directions, and restrained table styling, so the emphasis preserves the existing design language rather than introducing a new one.

Accepted remaining concern: supporting details, including case labels and non-promoted cell text, remain very small at this full-matrix screenshot scale. The p50 values are placed in SVG `<title>` tooltips, but tooltips are supplemental and may be undiscoverable or unavailable to touch, keyboard, and assistive-technology users; this screenshot cannot establish their accessibility or semantic exposure. Keep the visible score-point improvement; if p50 is essential for comparison, expose it through a persistent readable presentation or verify a usable equivalent, rather than relying on hover/title alone. No color, layout, or direction issue is newly evident. This is visual opposition only; no DOM/accessibility-tree or interaction inspection was performed.

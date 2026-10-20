# UI validation — latest comparisons

- **Before/after render:** captured with Firefox headless from the generated SVG at intrinsic width. Before and after screenshots are under `evidence/screenshots/`.
- **After review:** all four comparison columns remain; weighted/full index callouts are visible in same-flavor baseline headers; case rows retain suite bands, alternating backgrounds, and existing faster/slower colors.
- **Opposition:** the independent reviewer accepted the single-matrix consolidation and visual-language match; it flagged dense/small cell text at reduced screenshot scale. The 2200px-wide matrix intentionally retains the established density; no additional restyling was accepted because it would depart from the requested latest-section treatment.
- **Historical separation:** historical `1.8.0.0` comparison remains a separate chart and its generated winner is unchanged.
- **Layout:** single fixed-wide SVG matrix; no interactive controls, web-flow states, theme variants, or responsive widget behavior. Narrow-screen interactive checks are not applicable; the document embeds the intrinsic SVG.
- **Accessibility:** SVG title and `role="img"` remain present. No controls, motion, focus targets, or interaction changes.
- **Static checks:** focused diagram tests, full test suite, Ruff, Pyright, Mypy, generated-output freshness, and artifact immutability checks are required before completion.
- **Limitations:** the chart is intentionally dense; small case labels require zoom at reduced display widths, consistent with the prior latest-build matrix.

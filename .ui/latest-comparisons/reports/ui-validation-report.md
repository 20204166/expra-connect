# UI validation — latest comparisons

- **Before/after render:** captured with Firefox headless from the generated SVG at intrinsic width. Before and after screenshots are under `evidence/screenshots/`.
- **After review:** all four comparison columns remain; weighted/full index callouts are visible in same-flavor baseline headers; score points are now prominent bold 12px labels, with p50 timing detail in per-cell SVG titles; suite bands, alternating rows, and faster/slower colors are retained.
- **Opposition:** the independent reviewer accepted the score-first hierarchy and visual-language match. It notes that supporting case labels remain small and that SVG titles may be undiscoverable/unavailable to some input methods. The static screenshot does not establish assistive-technology exposure; treat timing titles as supplemental, not verified accessible detail.
- **Historical separation:** historical `1.8.0.0` comparison remains a separate chart and its generated winner is unchanged.
- **Layout:** single fixed-wide SVG matrix; no interactive controls, web-flow states, theme variants, or responsive widget behavior. Narrow-screen interactive checks are not applicable; the document embeds the intrinsic SVG.
- **Accessibility:** SVG title and `role="img"` remain present. No controls, motion, focus targets, or interaction changes. Per-cell timing detail is retained in SVG `<title>` tooltips, but keyboard/touch/screen-reader discovery was not verified.
- **Static checks:** focused diagram tests, full test suite, Ruff, Pyright, Mypy, generated-output freshness, and artifact immutability checks are required before completion.
- **Limitations:** the chart is intentionally dense; small case labels require zoom at reduced display widths, consistent with the prior latest-build matrix. Point ratios are always visible; raw p50 timing details are supplemental SVG titles.

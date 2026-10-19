# Design language — performance comparison

## Evidence-based inference

- **Tone:** utilitarian technical report; the chart prioritizes data comparison over decoration.
- **Density:** high, with a wide multi-version matrix and many compact rows.
- **Typography:** Arial/sans-serif; 22px title, 18px panel headers, 12px body/labels, 11px legend.
- **Colour:** white canvas, dark neutral text, blue-to-red diverging palette for ratios versus 1.8.0.0, gray for N/A. Blue indicates faster-than-reference buckets; red indicates slower buckets.
- **Geometry:** rectangular heatmap cells, tight gutters, no card/shadow treatment.
- **Interaction:** static SVG with `<title>` tooltips; no scripted interaction or motion.
- **Responsive behavior:** SVG has a fixed viewBox and intrinsic width 1385px; Markdown rendering may scale the whole graphic down on narrow screens.

Evidence: `scripts/update_performance_diagrams.py` (`PALETTE`, `_render_heatmap_svg`, `_svg_header`, `_text`) and rendered capture `evidence/screenshots/before/900px-chart.png`.

## Constraints for a future patch

- Preserve the existing graphic and all current suite panels; append rather than replace.
- Reuse the existing version order, ratio direction, typography, and palette semantics.
- Keep native and universal 2.2.0.4 as separate entries.
- Label the new values as unitless, lower-is-faster indices; do not imply absolute latency or a universal product winner.
- Retain explicit missing/unsupported status; never substitute zero.
- Avoid connecting points across sizes unless the workload mix is identical. Current size coverage changes suites/cases, so the audit recommends unconnected per-size point panels.
- Provide a concise textual explanation and a machine-readable accessible description for the appended plot; current SVG-level `role=img` does not expose the full data matrix as structured text.

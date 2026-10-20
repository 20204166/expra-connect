# UI validation — performance index axis

- Firefox headless screenshots captured before and after at SVG intrinsic size under `evidence/screenshots/`.
- The rendered index plots now show a `50.000p` tick at minimum and expand to `40.000p` when the lowest measured index (40.881p) falls below 50p. Previously the fixed 70p minimum placed sub-70p points outside the plot.
- Index values, baseline reference, series colors, and 100p marker are unchanged.
- Regression coverage checks a synthetic 45p minimum extends to 40p and still includes 50p.
- Full unittest suite: 980 tests passed. Ruff, Pyright, and Mypy passed; final formatting check passed after formatting the new test.
- Static SVG review only; no interactive, alternate-viewport, or assistive-technology review was performed.

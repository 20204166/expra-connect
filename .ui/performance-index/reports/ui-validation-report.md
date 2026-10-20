# UI validation — performance index axis

- Firefox headless screenshots captured before and after at SVG intrinsic size under `evidence/screenshots/`.
- The rendered index plots now reach `10.000p`, 30 points below the previous `40.000p` minimum. If future data falls below 10p, the axis expands to the next lower 10-point tick. The current lowest measured index (40.881p) remains in range.
- Index values, baseline reference, series colors, and 100p marker are unchanged.
- Regression coverage checks a synthetic 45p minimum includes 10p, 30p, 40p, and 50p ticks without extending unnecessarily to 0p.
- Full unittest suite: 980 tests passed. Ruff, Pyright, and Mypy passed; final formatting check passed after formatting the new test.
- Static SVG review only; no interactive, alternate-viewport, or assistive-technology review was performed.

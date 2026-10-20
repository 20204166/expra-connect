# UI change plan — performance index axis

1. Preserve the existing index values and 100.000p reference marker.
2. Lower the x-axis minimum to 50.000p; if the lowest observed point is below 50.000p, extend the minimum to the next lower 10-point tick.
3. Generate the SVG, inspect before/after renders, and test both the 50.000p floor and automatic lower extension.

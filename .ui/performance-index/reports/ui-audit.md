# UI audit — performance index axis

The canonical index plot in `scripts/update_performance_diagrams.py::_render_index_panel` hard-coded its horizontal minimum at 70.000p, even though the chart data and adjacent heatmap include values substantially below that. The generated index points could therefore render outside the plot's visible range. This is a chart-domain defect, not an index-calculation issue.

Accepted scope: keep all index values and scoring untouched; set the axis floor to at least 50.000p and extend lower to the next 10-point tick when measured data requires it.

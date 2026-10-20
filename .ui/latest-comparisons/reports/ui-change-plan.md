# UI change plan — latest comparisons

1. Rename the latest matrix title to **Latest comparisons** and describe all four comparison directions, using `2.2.1.0` native/universal as the respective same-flavor baselines.
2. Keep the established SVG geometry, typography, row density, and semantic score colors; add weighted/full-workload index callouts to the same-flavor baseline headers.
3. Remove the separate recent-reference SVG and its independent Markdown image/caption; retain historical comparison outputs.
4. Update tests for one consolidated output, correct denominator labels/score callouts, and cleanup of the obsolete SVG.

**Rollback:** restore the prior generator, test, Markdown, and SVG artifacts from the preceding commit; no benchmark JSON, calculations, or release files are changed by this visual patch.

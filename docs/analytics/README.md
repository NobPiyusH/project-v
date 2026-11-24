# Code analytics: old vs new (24 Nov 2026)

`tools/metrics/code_metrics.py REF --ruff-config pyproject.toml` counts one git ref without touching the working tree: files and
non-blank lines per language, test files and test functions, TODO notes, ruff findings (same rule set for both refs) and CI presence.

- **Old baseline:** `old-725d198.json` = main before the film pipeline, quality merge and Python migration (commit 725d198).
  Measured the same day on that code: Python tests 322 passed / 1 skipped, Java tests 395 passed / 3 skipped (counts above are
  test FUNCTIONS found by regex, so they are lower than parametrized pass counts).
- **New:** run the script on HEAD after the push and write `new-<sha>.json` next to it, then compare.

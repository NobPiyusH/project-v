# Code analytics: old vs new (24 Nov 2026)

`tools/metrics/code_metrics.py REF --ruff-config pyproject.toml` counts one git ref without touching the working tree: files and
non-blank lines per language, test files and test functions, TODO notes, ruff findings (same rule set for both refs) and CI presence.

- **Old baseline:** `old-725d198.json` = main before the film pipeline, quality merge and Python migration (commit 725d198).
  Measured the same day: worker tests about 286 passed (the 322 first quoted included the 36 new tool tests), Java tests 395 passed /
  3 skipped. Counts in the JSON are test FUNCTIONS found by regex, so parametrized tests make pass counts a little higher.
- **New:** run the script on HEAD after the push and write `new-<sha>.json` next to it, then compare.

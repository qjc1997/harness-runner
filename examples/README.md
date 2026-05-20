# examples/

Sample runs of harness-runner against real briefs, with the resulting
`feature_list.json`, `.harness_history.jsonl`, and shift logs preserved
as reference output.

**Status (2026-05-20)**: placeholder. Sample runs will land here once the
first stable end-to-end builds complete (URL shortener, mini-Hex).
Each example will include:

- the input `app_spec.md`
- the `feature_list.json` the Planner produced
- excerpts from `claude-progress.txt` showing the shift cadence
- the `.harness_history.jsonl` cost/duration trace
- the final running app(s) or a screenshot

These serve both as smoke tests for prompt regressions and as a portfolio
of what the harness can produce.

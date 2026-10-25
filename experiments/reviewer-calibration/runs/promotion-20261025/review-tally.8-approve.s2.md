```json
{"verdict":"APPROVE","findings":[
{"severity":"nit","pass":"spec","path":"factory/dispatch.py","line":233,"summary":"review_readiness runs and appends a review-readiness row for every tracked PR on every non-dry pass (plus a second row at line 271 on first intake), even when head/state are unchanged. record() is documented as a stage-transition trail; unconditional rows grow events.jsonl per pass and inflate the per-PR full-file read_events scans at lines 221/231. Consider recording only when (head, state) differs from the last readiness row."},
{"severity":"nit","pass":"spec","path":"factory/dispatch.py","line":274,"summary":"README.md:349 still states the review lane does not 'monitor CI'; this diff makes it query gh pr checks per pass. No documented rule mandates README/CHANGELOG updates, but the sentence is now false and CHANGELOG Unreleased entries exist for every sibling change."},
{"severity":"nit","pass":"bug","path":"factory/dispatch.py","line":307,"summary":"checks.returncode == 0 gate is inert: gh pr checks with --json writes the exporter and returns before the failed/pending exit-code logic (cli/cli pkg/cmd/pr/checks/checks.go), so rc is always 0 on success. Harmless (bucket checks already enforce all-pass), but the test row ('pass', rc 1 → ci_pending) encodes a scenario gh never produces."}
]}
```
VERDICT: APPROVE

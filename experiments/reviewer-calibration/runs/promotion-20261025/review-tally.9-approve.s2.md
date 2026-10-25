```json
{"verdict":"APPROVE","findings":[
{"severity":"nit","pass":"bug","path":"factory/dashboard.py","line":199,"summary":"`current.get(\"reason\")` is always None: `review-result` rows carry `findings`, not `reason` (dispatch.py:363). The `pr.reason` branch for `review_pending` in dashboard.html:86 is therefore dead; only `verdict === 'APPROVE'` ever admits a pending-review item to Inbox. Harmless, but misleading."},
{"severity":"nit","pass":"bug","path":"factory/dashboard.py","line":183,"summary":"State is recomputed from `checks` buckets instead of reading the `review-readiness` row's own `state` (dispatch.py:300-312 already encodes review_pending/changes_requested/ci_pending/ci_failed/ready for that head). Two classifiers for one event can drift; today they agree only because `readiness.state == 'ready'` is additionally required."},
{"severity":"nit","pass":"spec","path":"README.md","line":10,"summary":"CHANGELOG.md `## Unreleased` carries an entry per shipped feature with the issue number (e.g. #79, #67); this diff adds no entry for #9. Not in the issue's Touches list, so not blocking."}
]}
```

Plan drift: no plan recorded; all four changed files (`factory/dashboard.py`, `factory/dashboard.html`, `tests/test_factory.py`, `README.md`) are named in the issue's Touches. The exit-gate item "running `factory dashboard` displays the queue without changing labels, reviews, branches, or merge state" has no proof in the gate report beyond `test: PASS`; the diff's snapshot path issues only the read-only GraphQL query (dashboard.py:124-146) and the test asserts `events.jsonl` is unmodified (test_factory.py:284), so no mutation path is visible in the diff. Acceptance traced: six states classified per head-bound `review-result`/`review-readiness`/`escalate` rows matching dispatch.py:201-312; rows link `pr.url`; inbox items carry no mutation controls (dashboard.html:115-116).

VERDICT: APPROVE

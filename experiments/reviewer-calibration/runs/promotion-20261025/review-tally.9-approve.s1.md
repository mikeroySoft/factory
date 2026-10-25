```json
{"verdict":"APPROVE","findings":[
{"severity":"nit","pass":"spec","path":"factory/dashboard.py","line":836,"summary":"Inbox 'stalled' set omits a PR whose current head has a `review-intake` row but no `review-result` (reviewer failed/prompt_too_large). dispatch.py:264-266 never retries that head and no escalation fires until attempts exceed review_rounds+1, so it sits as 'Pending review' with no Inbox entry. Issue scope says 'include actionable stalled items in the existing inbox'; the six states don't model this, so judgment call — consider `review_pending` + intake-for-head-without-result as an inbox trigger."},
{"severity":"nit","pass":"bug","path":"factory/dashboard.py","line":857,"summary":"`current.get(\"reason\")` is always None: `review-result` rows carry `findings`, never `reason` (dispatch.py:363). The JS branch `pr.state === 'review_pending' && pr.reason` (dashboard.html:1161) is therefore dead; only the escalation reason ever surfaces."},
{"severity":"nit","pass":"bug","path":"factory/dashboard.py","line":823,"summary":"Admission reads `labels(first:10)` and `pullRequests(first:100)` (unfiltered by state, dashboard.py:150). dispatch admits via `gh pr list --state open --limit 1000` with full labels, so a PR beyond the newest 100 (merged agent PRs count) or with >10 labels is tracked by dispatch but absent from the queue. Pre-existing query bounds, newly relied on."},
{"severity":"nit","pass":"spec","path":"factory/dashboard.py","line":825,"summary":"104 chars; pyproject `[tool.ruff] line-length = 100`. Not CI-enforced and 27 other lines in this file already exceed it, so advisory only."}
]}
```

Plan drift: no plan recorded; changed files (README.md, factory/dashboard.html, factory/dashboard.py, tests/test_factory.py) match the issue's Touches list exactly. The issue's manual proof ("running `factory dashboard` displays the queue without changing labels/reviews/branches/merge state") is not in the gate report; the test's `EVENTS.read_bytes().startswith(before)` assertion (tests/test_factory.py:284) and the read-only GraphQL path cover the no-mutation half; the render half is unverified but no defect found tracing `render()` → `renderReviewQueue()` → `#review-queue` inside `<main id="ops">`.

VERDICT: APPROVE

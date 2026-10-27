```json
{"verdict":"APPROVE","findings":[
{"severity":"nit","pass":"bug","path":"factory/dashboard.py","line":199,"summary":"`current.get(\"reason\")` fallback is dead: `review-result` rows are recorded with only pr/head/verdict/findings (snapshot dispatch.py:363), so `reason` is only ever set when an `escalate` row exists, which already forces state=escalated. The matching inbox branch `pr.state === 'review_pending' && pr.reason` (dashboard.html diff line 86) is therefore unreachable; only the `verdict === 'APPROVE'` arm does work."},
{"severity":"nit","pass":"bug","path":"factory/dashboard.py","line":165,"summary":"Opt-in detection reads `labels(first:10)` from the dashboard GraphQL query, while dispatch intake uses `gh pr list --json labels` (all labels). A PR carrying `needs-review` beyond its first 10 labels would be admitted by dispatch but omitted from the queue. Low likelihood; pre-existing query cap, newly load-bearing."},
{"severity":"nit","pass":"spec","path":"README.md","line":10,"summary":"CHANGELOG.md has an `## Unreleased` section whose entries cite issue numbers; this user-visible dashboard feature adds no entry. No documented rule requires it (not in issue Touches), so not blocking."}
]}
```

Plan drift: no plan recorded. Changed files (README.md, factory/dashboard.py, factory/dashboard.html, tests/test_factory.py) match the issue's Touches list exactly. Gate report covers `test: PASS` only; the exit-gate clause "running `factory dashboard` displays the queue without changing labels/reviews/branches/merge state" has no manual proof, but the diff's only network call is the read-only GraphQL query and the test asserts `events.jsonl` is unchanged across `snapshot()` (diff line 284), so this exposes no defect.

Spec check: all required columns (number, title, author, head, last reviewed SHA, verdict, CI state, six states) present at dashboard.py diff 194–200; rows link to GitHub (dashboard.html diff 64); stalled items reach the inbox with no mutation controls (diff 84–90, 115–116); test covers six states, links and SHA-bound data (test_factory.py diff 286–299). Untrusted PR title/author/reason render via `h()` text nodes (snapshot dashboard.html:468), not innerHTML.

VERDICT: APPROVE

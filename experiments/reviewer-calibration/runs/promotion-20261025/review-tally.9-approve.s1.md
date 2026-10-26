```json
{"verdict":"APPROVE","findings":[
{"severity":"nit","pass":"bug","path":"factory/dashboard.py","line":857,"summary":"`current.get(\"reason\")` is always None: dispatch.py:363 records review-result rows with only verdict/findings, and a prompt_too_large/unparsed skip records no review-result at all (dispatch.py:335-356). So the `pr.reason` inbox branch at dashboard.html:1162 is dead, and a PR whose review was skipped (not retried per README:346-348) sits as 'Pending review' with no Inbox entry, contrary to README.md:362-364 ('pending reviews needing human attention also appear in Inbox'). Either drop the branch or detect review-intake-at-head without a review-result."},
{"severity":"nit","pass":"spec","path":"factory/dashboard.html","line":1162,"summary":"review_pending with an old APPROVE is pushed to Inbox as waiting-on-you, but dispatch re-reviews the new head automatically on its next pass (dispatch.py:264-271); it is not stalled. Adds Inbox noise/badge count without a human action."},
{"severity":"nit","pass":"bug","path":"factory/dashboard.py","line":125,"summary":"`viewer{login}` added to the single snapshot GraphQL query; github() raises on any `errors` (dashboard.py:224-225), so a token that cannot resolve viewer drops the whole GitHub snapshot (issues, PRs, upstream), not just review-request admission. Dispatch resolves login lazily only when review requests exist (dispatch.py:213-216)."},
{"severity":"nit","pass":"spec","path":"CHANGELOG.md","line":3,"summary":"Unreleased section not updated for the new review queue/snapshot key, unlike sibling features listed with (#N); no documented rule requires it."}
]}
```

Plan drift: plan not recorded; all four changed files are the issue's named Touches. Gate report covers tests only; the issue's manual exit check ("running `factory dashboard` displays the queue without changing labels/reviews/branches/merge state") has no recorded proof — the snapshot test's `EVENTS.read_bytes().startswith(before)` plus no mutation path in `review_queue`/`snapshot()` is the only evidence.

VERDICT: APPROVE

```json
{"verdict":"APPROVE","findings":[
{"severity":"nit","pass":"spec","path":"factory/dispatch.py","line":37,"summary":"Re-implements the `gh pr checks --json name,bucket` query + fail-closed JSON parse that `pr_checks()` (dispatch.py:978) already provides; a `required: bool` parameter on the existing helper would avoid the duplicate parser and bucket contract."},
{"severity":"nit","pass":"bug","path":"factory/dispatch.py","line":46,"summary":"`json.loads(checks.stdout)` runs before the fresh-head parse inside one try; an unparsable checks payload (e.g. gh's non-JSON 'no required checks' error) skips head confirmation, so the recorded `head` can be the stale list head. Not ready-affecting (ready already requires `confirmed`), but the event's head/review_head can misreport which revision was evaluated."},
{"severity":"nit","pass":"bug","path":"factory/dispatch.py","line":72,"summary":"A `review-readiness` row with the full `checks` payload is appended for every tracked PR on every dispatch pass (call sites :24 and :32) regardless of change; bounded consumers (runtime-json event_limit 512 / 1 MiB, briefing event history) will be dominated by repeated identical rows. Skip the record when the last `review-readiness` for the PR has the same head/state."},
{"severity":"nit","pass":"spec","path":"factory/dispatch.py","line":15,"summary":"README.md:335-349 documents lane discovery as label or pending review request and states the lane does not 'monitor CI'; this diff now keeps once-APPROVED PRs tracked and queries CI each pass, and adds an undocumented `review-readiness` event (README.md:378-381 audit-trail list). No CHANGELOG Unreleased entry (#8) either, unlike sibling features."}
]}
```
VERDICT: APPROVE

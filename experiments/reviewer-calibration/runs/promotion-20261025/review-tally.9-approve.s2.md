## Standards
No `AGENTS.md`/`CONTRIBUTING.md` in the repo; `docs/` holds plans only. Nothing documented is violated. Event names, field keys (`pr`, `head`, `verdict`, `checks[].bucket`, `state`, `at`), opt-in rules, and the CI bucket → state mapping in `factory/dashboard.py:811-859` mirror `factory/dispatch.py:201-312` exactly (label OR viewer review-request OR prior verdict; escalation terminal; `ready` only when readiness recorded `ready` and all required buckets `pass`). `h()` supports `style` objects (`factory/dashboard.html:457`). Inbox review items are in `WAITING`, so the informational branch that dereferences `i.t.number` (`dashboard.html:1559`) never sees them.

## Spec (issue #9)
- Six states, PR number/title/author/head/last-reviewed SHA/verdict/CI state: `dashboard.py:852-857`, rendered at `dashboard.html:1131-1142`. ✓
- GitHub link per row: `dashboard.html:1133`. ✓
- Stalled items in existing inbox, no mutation controls: `dashboard.html:1160-1166`, `1550-1551` (links only). ✓
- Test covers all six states, PR URLs, SHA-specific review data (old-head approval → `review_pending`, `ci_state: unknown`), and asserts `events.jsonl` unchanged: `tests/test_factory.py:2778-2840`. ✓ Gate `test: PASS`.
- README documents the lane: `README.md:353-365`. ✓
- Out-of-scope items untouched.

## Plan drift
Plan not recorded. Files changed are exactly the four the issue's "Touches" names. Manual exit-gate item ("running `factory dashboard` displays the queue") is not machine-verifiable from the diff; the snapshot test plus unchanged-journal assertion is the available proof.

```json
{"verdict":"APPROVE","findings":[{"severity":"nit","pass":"bug","path":"factory/dashboard.py","line":857,"summary":"`current.get(\"reason\")` is a dead fallback: `review-result` rows are recorded with pr/head/verdict/findings only (dispatch.py:363), so `reason` is only ever populated from escalation."}]}
```
VERDICT: APPROVE

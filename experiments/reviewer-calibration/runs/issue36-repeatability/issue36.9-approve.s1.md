# Review: #9 external PR review queue (`311ba91..45d96f6`)

## Standards

No AGENTS.md/CONTRIBUTING.md at this head; README is the only documented convention. Relevant rules checked:

- README "Opt-in viability": `needs-review` opts a PR into intake — `factory/dashboard.py:826-833` uses `LABEL_REVIEW`, viewer review request, or prior verdicted `review-result`. Consistent.
- README "Dashboard … never executes / read-only snapshot": `review_queue` (`factory/dashboard.py:811-859`) is a pure projection over `prs`/`rows`; no `gh` mutation, no event append. Test asserts the journal is only appended-to, never rewritten (`tests/test_factory.py:2823`).
- README "Review" rule that Inbox links rather than mutates: review inbox case renders `reviewRow` + explanatory text, no buttons (`factory/dashboard.html:1551-1552`).
- `python -P`/import conventions untouched.

## Spec (issue #9 + brief)

| Criterion | Evidence | Status |
|---|---|---|
| Six states | `factory/dashboard.py:843-852`; test asserts all six (`tests/test_factory.py:2828-2830`) | ✓ |
| Number, title, author, head SHA, last reviewed SHA, verdict, CI state | `factory/dashboard.py:853-859`; rendered `factory/dashboard.html:1134-1143` | ✓ |
| Link each row to GitHub | `factory/dashboard.html:1136` `href: pr.url`; test checks URL per PR (`tests/test_factory.py:2832`) | ✓ |
| SHA-specific review data | `current` bound to `headRefOid` (`factory/dashboard.py:835-836`); PR 1 old-head APPROVE → `review_pending`, `ci_state: unknown` (`tests/test_factory.py:2836-2838`) | ✓ |
| Stalled items in existing inbox, no mutation controls | `factory/dashboard.html:1160-1166`, `1551-1552` | ✓ |
| README documents the lane | `README.md:349-365` | ✓ |
| No new dashboard service / no CI poll | Reuses `GRAPHQL` query (`factory/dashboard.py:124,154-155`) and `review-readiness` events; no new transport | ✓ |

## Required fixes

None.

## Optional suggestions

1. **Admission can drop an opted-in PR after GitHub consumes the request.** `factory/dashboard.py:826-833` keeps a label-less, request-consumed PR only if some `review-result` has verdict `APPROVE`/`REQUEST_CHANGES`. A PR whose only recorded result is `unknown` (e.g. `prompt_too_large`, README:347-349) disappears from the queue, although README:353-354 (this diff) says such admissions are listed. README:338-339 documents `review-intake` events with `pr`/`head` as the admission record; keying admission on any `review-intake` row for the PR would be simpler and complete. Not blocking: the issue text does not define opt-in persistence, and the test's consumed-request case (PR 5) passes.
2. **`escalated` is not head-bound.** `factory/dashboard.py:839` takes the latest `escalate` event for the PR regardless of `head`, so a PR that was escalated, then re-pushed and re-admitted, stays `escalated` even after a fresh `REQUEST_CHANGES`/`APPROVE` on the new head (`:851-852` overrides). Since `current`/`readiness` are head-bound (`:835-838`), binding escalation the same way (`e.get("head") == head`) is a one-token change. Depends on #8's escalate payload, which is outside this packet — `[INFERENCE]`.
3. **APPROVE with no readiness evidence shows "CI pending".** `factory/dashboard.py:847` maps `ci == "unknown"` to `ci_pending` while the row prints `Required CI: unknown` (`factory/dashboard.html:1142`). README:356-357 says missing evidence is unknown. Cosmetic inconsistency; the six-state enum has no "unknown" slot, so leaving as-is is defensible.
4. `labels(first:10)` (`factory/dashboard.py:156`, pre-existing) can hide `needs-review` on heavily labelled PRs; suggestion 1 would also remove that dependency.

Verification evidence: gate `test: PASS` on target host; snapshot test covers states, URLs, heads, authors, SHA-bound review data, exclusion of closed/draft/other-reviewer PRs (`tests/test_factory.py:2828-2841`). Diff-only review; no local execution.

VERDICT: APPROVE

## Spec (issue #8)

Acceptance criteria mapped to the diff:

| Criterion | Evidence |
|---|---|
| Distinct states `ci_pending` / `ci_failed` / `changes_requested` / `ready` | `factory/dispatch.py:300-309` |
| Missing / unparsable / pending / cancelled / failed → not ready | `valid` requires non-empty list of known buckets (`factory/dispatch.py:293-296`); `ready` additionally requires `confirmed`, `checks.returncode == 0`, all `pass` (`factory/dispatch.py:307-309`). Tests: `tests/test_factory.py:1392-1404` (`[]`, non-string bucket, unknown bucket, `not json`, `null`, `[{}]`, pending, fail, cancel, skipping, pass-with-nonzero-exit all ≠ `ready`). |
| Approval + passing required checks for same head → ready | `factory/dispatch.py:297-299` binds review lookup to `head`; `tests/test_factory.py:1405`. `--required` is asserted at `tests/test_factory.py:1382`. |
| New head SHA clears readiness | Fresh `headRefOid` re-read replaces `head` before the review lookup (`factory/dispatch.py:288-290`, `297-299`); `tests/test_factory.py:1413-1419` covers both the mid-query push and the listing update. |
| No `gh pr merge` for external PR | `review_readiness` only records (`factory/dispatch.py:310`); `tests/test_factory.py:1426`. |
| Events via existing `record()` | `factory/dispatch.py:310`. |
| Dry run writes nothing | guards at `factory/dispatch.py:232-233`, `268-271`; `tests/test_factory.py:1423-1425`. |

Fail-closed properties hold: any parse/exit anomaly leaves `valid=False` or `confirmed=False`, and the only path to `ready` requires all four conditions. `ci_failed` can be reported without `confirmed`, but never `ready`, so no acceptance criterion is affected.

Intake eligibility widening to `APPROVE` (`factory/dispatch.py:220`) is required so approved PRs keep being tracked after GitHub consumes the review request; the subsequent `continue` at `factory/dispatch.py:234-236` still prevents re-review.

## Standards (README)

No AGENTS.md/CONTRIBUTING.md at this head. Non-blocking observation: README's review-lane paragraph still states the lane "does not … monitor CI"; this diff makes that sentence stale. README is not in the issue's Touches list and the statement is a behavior description rather than a documented convention, so not a REVISE finding — suggest a one-line README update in a follow-up.

VERDICT: APPROVE

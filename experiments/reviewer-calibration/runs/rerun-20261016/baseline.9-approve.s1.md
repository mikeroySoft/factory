## Spec (issue #9)

- Snapshot fields: number, title, url, author, head, review_head, verdict, ci_state, state — `factory/dashboard.py:852-858`. All seven required attributes plus one of the six states present.
- Six-state classification: `factory/dashboard.py:843-851`. `review_pending` default, `changes_requested`/`ci_pending`/`ci_failed`/`ready` from the head-bound review-result + readiness, `escalated` override.
- SHA-specific review data: `current` is filtered on `e.get("head") == head` (`factory/dashboard.py:834-835`); `review_head`/`verdict` come from the latest review regardless of head (`:821`), so an old approval shows as evidence but does not advance the state. Matches README claim "An old approval never makes a new head ready" (`README.md:361`).
- GitHub link per row: `factory/dashboard.html:1135`. Inbox items carry `review: pr` and render via `reviewRow` with the same link (`:1164`, `:1550-1551`); no buttons beyond existing Previous/Next navigation — no mutation controls added.
- Test covers all six states (`tests/test_factory.py:2826-2828`), PR URLs and head SHAs per row (`:2830-2831`), old-head review identity (`:2834-2836`), and asserts the journal is unchanged after `snapshot()` (`:2823`). Satisfies exit gate as far as the diff can show; gate reports `test: PASS`.
- README documents the lane and the no-mutation guarantee (`README.md:354-365`).

## Standards

No AGENTS.md/CONTRIBUTING.md at this head; README conventions on `needs-review`/review-request opt-in (`README.md:332-333`) are mirrored in `factory/dashboard.py:823-831` (label, viewer review request, or prior recorded verdict). Test asserts consumed review requests do not drop an admitted PR (`tests/test_factory.py:2808-2809`).

## Non-blocking observations

- `escalation` is the latest `escalate` row for the PR with no head filter (`factory/dashboard.py:838`), so a later push with a fresh approval still reports `escalated`. The issue does not define escalation as SHA-bound, so this does not fail acceptance; noting only because every other state input is head-bound.
- `ci == "pass"` with `readiness.state != "ready"` yields `ci_pending` (`factory/dashboard.py:848-850`) while the row shows `Required CI: pass`. Depends on the readiness-event contract, which is outside this packet; not citable as a defect.

VERDICT: APPROVE

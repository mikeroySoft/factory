## Standards

No `AGENTS.md`/`CONTRIBUTING.md` in the snapshot; conventions come from `README.md`, `.github/ISSUE_TEMPLATE/agent_task.md`, and existing code shape. The diff matches surrounding style: pure projection function next to `build_ticket` (`factory/dashboard.py:153-201`), DOM via the existing `h()` helper (style-object form is supported, snapshot `dashboard.html:464`), inbox item shape reused (`factory/dashboard.html:84-90`). Labels resolved through `LABEL_REVIEW` from `factory.config` (`factory/dashboard.py:128`), not a string literal. No hard violations.

Smell notes (judgement calls, non-blocking):
- `review_queue` admission logic (`factory/dashboard.py:165-174`) duplicates the shape of `dispatch.review_intake_pass` (snapshot `dispatch.py:211-223`), and the state ladder (`:185-193`) mirrors `dispatch.review_readiness` (`:300-310`). Acceptable here: the dashboard reads recorded events instead of calling `gh`, so sharing one function would require threading live `gh` results through a read-only path.

## Spec (issue #9 + agent brief)

Satisfied:
- Six states classified, SHA-bound: review/readiness looked up by `head == pr["headRefOid"]` (`factory/dashboard.py:175-179`); an old approval on a different head yields `review_pending` with `ci_state: unknown` (test `tests/test_factory.py:261-263, 286-296`). Matches README claim "An old approval never makes a new head ready" (`README.md:21`).
- Row fields: number, title, author, head, last reviewed SHA, verdict, CI state, state (`factory/dashboard.py:194-200`; rendered `factory/dashboard.html:62-71`).
- GitHub link per row (`factory/dashboard.html:64`); test verifies `url` per PR (`tests/test_factory.py:290`).
- Inbox: changes_requested/ci_failed/escalated plus stale-approved pending reviews (`factory/dashboard.html:84-90`). Stale-approve inclusion is correct: dispatch never re-reviews once any APPROVE exists (snapshot `dispatch.py:234-236`), so a new head is human-attention. No mutation controls; briefing text links to GitHub only (`factory/dashboard.html:115-116`).
- Read-only: new GraphQL fields are reads (`factory/dashboard.py:136,144-145`); `review_queue` performs no I/O; test asserts `events.jsonl` unchanged (`tests/test_factory.py:276,284`).
- Opt-in parity with dispatch: `needs-review` label, review request to the viewer (case-folded), or prior review-result after GitHub consumed the request (`factory/dashboard.py:165-174`; test `:253-258, 273-274`).
- Exit gate test covers all six states (`tests/test_factory.py:286-288`); gate `test: PASS`.
- README documents the lane (`README.md:10-26`).

Out-of-scope boundaries respected: no review submission, branch edits, merge, or separate service.

## Plan drift

Plan not recorded; nothing to diff against. Files changed (`README.md`, `factory/dashboard.py`, `factory/dashboard.html`, `tests/test_factory.py`) are exactly the issue's "Touches" list. No planned proof to check beyond the exit gate's `python -m unittest`, which the gate reports PASS. No drift exposing a defect.

## Required fixes

None.

## Optional suggestions

1. `factory/dashboard.py:199` — `current.get("reason")` is always `None`: `review-result` rows carry `findings`, not `reason` (snapshot `dispatch.py:363`). Consequently the `pr.reason` clause at `factory/dashboard.html:86` is dead for `review_pending`. Drop `or current.get("reason")` and the `pr.reason ||` test, or use `findings` if you intend to surface them.
2. `factory/dashboard.py:217` — queue is sourced from the dashboard's `pullRequests(first:100, CREATED_AT DESC)` across all states (snapshot `dashboard.py:150`), while dispatch intake scans up to 1000 open PRs (`dispatch.py:204-206`). In a repo with >100 PRs newer than an open opted-in PR, that PR (including an escalated one) silently disappears from the queue and Inbox while `README.md:14` says the panel "lists open, non-draft opted-in PRs". Inherited from the existing snapshot bound (snapshot `dashboard.py:945-946`), so not blocking; a `pageInfo.hasNextPage` → "queue may be truncated" note in the panel would be the minimal honest signal.
3. `CHANGELOG.md` "Unreleased" has no entry for #9; every sibling feature in that section cites its issue. Not a documented rule, so non-blocking.

VERDICT: APPROVE

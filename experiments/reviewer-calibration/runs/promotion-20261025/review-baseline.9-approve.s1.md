## Standards

No `AGENTS.md`/`CONTRIBUTING.md` in the snapshot; conventions come from `README.md`, `.github/ISSUE_TEMPLATE/agent_task.md`, and `CHANGELOG.md`.

- Read-only contract honored: `review_queue()` is a pure projection over the already-materialized journal rows (`review.diff:153-201`); the test asserts the journal is byte-unchanged after `snapshot()` (`review.diff:276-284`). Matches README "Viewing or refreshing the queue does not change labels, reviews, branches, or merge state" (`review.diff:25-26`).
- Classification mirrors the producer in `factory/dispatch.py:review_readiness` (same `fail`/`cancel` → failed, `ready` only when dispatch recorded `state == "ready"` for the confirmed head, escalation terminal as in `review_intake_pass`), so the dashboard cannot disagree with dispatch (`review.diff:181-193`).
- Opt-in rule reproduces dispatch's three-way admission (label, review request to the viewer, or prior verdict) (`review.diff:165-174`), including the consumed-review-request case the test exercises (`review.diff:273-274`).
- New JS uses existing helpers (`h`, `fmt`, `.empty`, `briefing-option`) and the existing `S.q` search contract; `h()` already accepts the `style` object and drops `null` kids (`snapshot/factory/dashboard.html:455-468`).

## Spec (issue #9)

| Criterion | Evidence |
|---|---|
| Opted-in PR rows: number, title, author, head SHA, last-reviewed SHA, verdict, CI state, one of six states | `review.diff:194-200` (snapshot), `review.diff:62-71` (render) |
| Row links to GitHub | `review.diff:64`, asserted at `review.diff:290` |
| Stalled items in existing Inbox, no mutation controls | `review.diff:84-90`, `review.diff:115-116` (GitHub links only; no form/buttons) |
| Snapshot test: six states, PR links, SHA-specific data | `review.diff:286-298` |
| README operating description | `review.diff:14-26` |
| Out of scope: no review submission/branch fixes/merges/separate service | none added |

Gate: test PASS on target host.

## Plan drift

No plan recorded. Files changed (`README.md`, `factory/dashboard.py`, `factory/dashboard.html`, `tests/test_factory.py`) are exactly the issue's "Touches" list. `README.md:349-351` also rewrites the pre-existing "does not … monitor CI" sentence to reflect #8's readiness/escalation behavior (`review.diff:9-12`); that is doc correction inside the same section and consistent with `snapshot/factory/dispatch.py:274-312`. No defect exposed.

## Required fixes

None.

## Optional suggestions

- `review.diff:199` — `current.get("reason")` is always `None`: `review-result` rows carry `findings`, not `reason` (`snapshot/factory/dispatch.py:363`). Consequently the `pr.reason` branch in the inbox predicate at `review.diff:86` is dead for `review_pending`; `escalation.get("reason")` alone suffices.
- `review.diff:86` — `review_pending && verdict === 'APPROVE'` (old approval, new head) is surfaced as "waiting on you", yet dispatch re-reviews that head automatically on its next pass (`snapshot/factory/dispatch.py:264-271`). Consider restricting inbox inclusion to `changes_requested`/`ci_failed`/`escalated` so the badge count only reflects human-required cases.
- `CHANGELOG.md` `## Unreleased` lists each feature with its issue number (`snapshot/CHANGELOG.md:5-13`); no entry was added for #9. Not a documented rule, so non-blocking.

VERDICT: APPROVE

## Standards

Sources checked: no `AGENTS.md`/`CONTRIBUTING.md` in snapshot; `README.md` and `CHANGELOG.md` are the only documentation. No documented coding rule is violated. Code matches surrounding style (`run(..., check=False)` + `json.loads` pattern as in `open_blockers`, events via `record()`).

**Optional**
- `review.diff:35-73` — README:349 still states the review lane "does not … monitor CI". The diff makes that false. No documented rule mandates README updates, so non-blocking, but the sentence now contradicts behavior; amend it.
- `review.diff:35` — `CHANGELOG.md` `Unreleased` lists entries per issue (#79, #67, #62); none added for #8. Convention by example only, not a stated rule.
- `review.diff:23-24` and `:32` — `review_readiness` runs on every dispatch pass for every tracked PR (now including approved, non-opted-in PRs via `:15`), appending a `review-readiness` row and issuing two `gh` calls each time even when head/state are unchanged. For a new head, two rows land in one pass (`:24` before intake, `:32` after review). Unbounded `events.jsonl` growth for long-lived approved PRs; skipping the append when the last `review-readiness` for the same `head` has the same `state` would bound it. Not a spec criterion.
- `review.diff:53-57` — a single row with an unrecognized `bucket` makes `valid` false, so a sibling `fail` row is reported as `ci_pending` rather than `ci_failed`. Still not-ready (fail-closed), only the label is less precise.
- `review.diff:73` — `review_head` is always either `head` or `None` because the lookup at `:58-60` already filters on `head`; the field carries no extra information.

## Spec (issue #8 + triage brief)

- Distinct states `ci_pending`, `ci_failed`, `changes_requested`, `ready` (plus `review_pending`): `review.diff:61-71` ✓
- Ready requires approval for the current head AND all required checks pass: `:58-60` binds review to `head`; `:68-71` requires `confirmed` (fresh `headRefOid == head`), `valid`, exit 0, all `pass` ✓
- Missing / unparsable / pending / failed / cancelled not ready: `:53-57`, `:66-67`; tests `:113-127` cover `[]`, `not json`, `null`, `[{}]`, `pending`, `fail`, `cancel`, `skipping`, bad-bucket types ✓
- New head clears readiness: `:48-50` resets `head` to fresh value → no matching review → `review_pending`; tests `:134-141` ✓
- No `gh pr merge` for external PRs: test `:148` ✓; no merge call in `review_readiness`
- Events via existing `record()`: `:72-73` ✓
- Files touched match issue "Touches": `factory/dispatch.py`, `tests/test_factory.py` ✓; dashboard untouched ✓
- `python -m unittest`: gate `test: PASS`

Race ordering is sound: checks (`:37-40`) are queried before the fresh head (`:41-43`), so a push between the two yields `confirmed=False`; a push before both yields a head with no review. Parse failure of `checks.stdout` (`:46`) short-circuits before `current` is parsed, leaving `confirmed=False` — fail-closed, covered by `:119`.

No missing or wrong requirements found.

## Plan drift

Plan status not recorded; no planned file list or proof to diff against. Changed files (`factory/dispatch.py`, `tests/test_factory.py`) are exactly the issue's "Touches" list. Gate report covers the issue's verification command. No drift exposing a defect.

## Required fixes

None.

VERDICT: APPROVE

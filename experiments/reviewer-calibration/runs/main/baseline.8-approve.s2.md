## Spec (issue #8)

- Distinct states derived fail-closed: `review_pending` / `changes_requested` / `ci_pending` / `ci_failed` / `ready` — `factory/dispatch.py:300-309`. `ready` requires approval bound to the freshly re-read head (`:297-299`, `:307`), `confirmed` head match (`:288`), valid rows (`:292-296`), exit 0 and all `pass` (`:307-309`).
- Missing/unparsable checks fail closed: empty list rejected by `bool(rows)` (`:292`); non-JSON leaves `rows=[]`, `confirmed=False` (`:284-291`), so neither `ready` nor `ci_failed` can be reached — events still record `checks=[]` (`:310-311`).
- Pending/failed/cancelled: `fail`/`cancel` → `ci_failed` (`:305-306`); `pending`/`skipping` stay `ci_pending` (`:304`).
- Later head SHA clears readiness: review lookup keys on the re-read head (`:289`, `:297-299`); a race between the PR listing and the readiness read is covered (`tests/test_factory.py:1409-1411`).
- No merge: only `gh pr checks` and `gh pr view` are issued (`:276-282`); asserted at `tests/test_factory.py:1425`.
- Events go through existing `record()` (`:310`); dry run records nothing (`:232-233`, `tests/test_factory.py:1422-1424`).
- Tests cover missing, unparsable, pending, failed, cancelled, passing, stale-approval and merge-absence (`tests/test_factory.py:1391-1425`). Gate reports `test: PASS`.

## Standards (README)

- Eligibility widening to approved PRs (`factory/dispatch.py:220`) is required to keep tracking after GitHub consumes the review request; consistent with the intake description in README.

## Non-blocking

- README states the review lane "does not … monitor CI" and lists only `review-intake` events for this lane; `factory/dispatch.py:276-282` now queries check state and `:310` emits a new `review-readiness` event. README is outside the issue's `Touches` list, so not a blocking criterion here, but the sentence is now inaccurate and the new event name is undocumented.
- Each pass issues two `gh` calls per ever-approved open PR (`:220`, `:232-233`); advisory only, no correctness impact.

VERDICT: APPROVE

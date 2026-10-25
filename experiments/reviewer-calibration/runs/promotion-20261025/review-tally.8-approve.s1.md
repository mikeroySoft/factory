**Standards.** No AGENTS.md/CONTRIBUTING.md in the snapshot; conventions come from README/CHANGELOG. Code matches existing patterns (`run(check=False)`, `record()` kwargs, `lifecycle.read_events`). Doc drift: README:349 still states the review lane "does not … monitor CI", and `review-readiness` is absent from the README event list and CHANGELOG `Unreleased` — not a documented rule, so nit.

**Spec (issue #8 exit gate).** Traced `review_intake_pass` → `review_readiness` → `record`:
- Missing (`[]`), unparsable, pending, `skipping` → `ci_pending`; `fail`/`cancel` → `ci_failed`; never `ready` (dispatch.py:292-310). ✓
- `ready` requires latest `review-result` APPROVE for the *re-fetched* head, `confirmed` head equality, all rows `pass`, and `gh pr checks` exit 0 (dispatch.py:287-289, 307-310). ✓
- New head: review lookup keyed by head (dispatch.py:297-299) → `review_pending`; a mid-query push re-keys `head` to the fresh SHA so stale approval cannot leak (dispatch.py:288-289). ✓
- No `gh pr merge` anywhere in the diff; test asserts it (tests/test_factory.py:1422). ✓
- Events via existing `record()` (dispatch.py:311). ✓
- Test covers missing/unparsable/pending/failed/cancelled/ready/stale-head/dry-run (tests/test_factory.py:1385-1421). ✓

**Plan drift.** No plan recorded. Files changed (`factory/dispatch.py`, `tests/test_factory.py`) are exactly the issue's "Touches". Verification `python -m unittest` is covered by gate `test: PASS`. No missing proof.

```json
{"verdict":"APPROVE","findings":[
{"severity":"nit","pass":"bug","path":"factory/dispatch.py","line":233,"summary":"review_readiness runs and appends a review-readiness row for every tracked PR on every pass, and twice in the pass that admits a new head (line 233 before review, line 271 after). The append-only journal grows per pass with no state change; the 233 call on a newly admitted head always records review_pending noise. Consider skipping the record when state/head match the last review-readiness row for that PR."},
{"severity":"nit","pass":"spec","path":"factory/dispatch.py","line":274,"summary":"Lane now queries gh pr checks, but README.md:349 still says the review lane 'does not … monitor CI', and the new review-readiness event is not listed in the README event list or CHANGELOG Unreleased, unlike other Unreleased entries."},
{"severity":"pre-existing","pass":"spec","path":"factory/dispatch.py","line":234,"summary":"Any prior APPROVE ends the review cycle regardless of head, so a head pushed after approval is never re-reviewed and can only ever report review_pending; readiness is correctly cleared per the issue, but no path back to ready exists without human action. Pre-existing policy (test_external_review_head_transitions_end_on_approval), not introduced here."}
]}
```
VERDICT: APPROVE

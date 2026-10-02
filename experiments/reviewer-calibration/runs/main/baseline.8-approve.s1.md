## Spec (issue #8)

| Criterion | Evidence | Status |
|---|---|---|
| Distinct states pending / failed / changes-requested / ready | `factory/dispatch.py:300-310` | met |
| Missing / unparsable / pending / failed / cancelled → not ready | `factory/dispatch.py:292-296` (`valid` requires non-empty list with known buckets), `:305-306` (`fail`/`cancel` → `ci_failed`), `:307-309` (`ready` needs `confirmed and valid and returncode == 0 and all pass`); unparsable stdout raises at `:285` leaving `confirmed=False` (`:283`) | met, fail-closed |
| Ready only for approval + passing required checks on same head | `:297-299` matches `review-result` on the re-read head; `:276-278` passes `--required`; `:287-289` sets `confirmed` only when live head equals the admitted head | met |
| New head SHA clears readiness | `:288-289` replaces `head` with the live SHA, so the `:297` review lookup misses the old approval → `review_pending`; test `tests/test_factory.py:1411-1418` | met |
| No `gh pr merge` for external PR | no merge call in the diff; asserted at `tests/test_factory.py:1426` | met |
| Events via existing `record()` | `factory/dispatch.py:311-312` | met |
| Opted-in PRs stay tracked after GitHub consumes the review request | `factory/dispatch.py:217-221` now includes `APPROVE` | required for post-approval tracking |

## Standards (README)

No AGENTS.md/CONTRIBUTING.md at this head. No convention violation in the diff.

## Non-blocking notes

- `factory/dispatch.py:232-233` runs `review_readiness` for every tracked PR before the already-approved `continue`, and `:271` runs it again after a fresh review. A newly admitted PR therefore emits two `review-readiness` rows per pass (first one `review_pending` before `review-intake` is recorded), and every tracked PR costs two `gh` calls per pass. Not an acceptance-criteria failure; journal growth only.
- README states "This lane does not change contributor branches, monitor CI, or merge external PRs." The diff (`factory/dispatch.py:276-279`) now monitors CI for this lane; README is outside the issue's `Touches` list, but the sentence is now stale and should be corrected in a follow-up.

VERDICT: APPROVE

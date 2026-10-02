# Review: #8 — CI / merge-readiness tracking for the review lane

## Spec compliance (issue #8 exit gate)

| Criterion | Evidence | Status |
|---|---|---|
| Missing checks not ready | `factory/dispatch.py:293-297` `valid` requires non-empty list; test `tests/test_factory.py:1391` (`"[]"` → `ci_pending`) | met |
| Unparsable checks not ready | `dispatch.py:285-291` parse failure leaves `rows=[]`, `confirmed=False`; tests `:1392-1393,1396-1398` | met |
| Pending not ready | `dispatch.py:306-308` ready requires all `pass`; tests `:1394,1399` | met |
| Failed / cancelled → `ci_failed` | `dispatch.py:304-305`; tests `:1395,1400-1401` | met |
| Approval + passing required checks, same head → `ready` | `dispatch.py:306-309` (`confirmed and valid and returncode==0 and all pass`); test `:1404` | met |
| New head SHA clears readiness | `dispatch.py:288-290` re-reads `headRefOid`, looks up review by `head` (`:298-300`); tests `:1411-1417` cover both mid-query push and new listed head | met |
| Changes requested distinct state | `dispatch.py:302-303`; test `:1418-1420` | met |
| No `gh pr merge` for external PR | `review_readiness` issues only `gh pr checks` / `gh pr view`; test asserts `:1425` | met |
| Events via `record()` | `dispatch.py:310-311` `review-readiness` event with `pr`, `head`, `state`, `review_head`, `checks` | met |
| `--required` flag used | `dispatch.py:276-278`; asserted `tests:1381` | met |
| Dry run writes nothing | `dispatch.py:232-233` guard; test `:1421-1423` | met |

Fail-closed properties hold: a nonzero `gh pr view` exit, a non-dict payload, or a parse failure all keep `confirmed=False`, so `ready` is unreachable (`dispatch.py:288-291, 306`).

## Required fixes

None.

## Optional suggestions

1. **`factory/dispatch.py:284-291` — coupled parsing.** `json.loads(checks.stdout)` runs before `json.loads(fresh.stdout)` in one `try`; an unparsable checks payload skips the head refresh, so the event records the stale `head` and `ci_pending` instead of `review_pending` when a push also landed (test `:1396` only covers the no-push case). Still never `ready`, so not a defect; parsing `fresh` first (or separately) would make the recorded head accurate.
2. **`factory/dispatch.py:306-308` — `skipping` bucket.** A required check reported as `skipping` yields permanent `ci_pending` (test `:1402`). Fail-closed and defensible; worth a one-line comment since GitHub treats skipped required checks as satisfied in some rulesets.
3. **`factory/dispatch.py:232-233, 271`** — every pass appends a `review-readiness` row per tracked PR, and the relaxed `opted_in` filter at `:217-221` now tracks approved PRs until close. Unbounded but consistent with the append-only journal contract; a change-only emit (compare with last recorded state) would reduce growth. Not requested by the brief.
4. **README.md** — "This lane does not change contributor branches, monitor CI, or merge external PRs" (Configuration section) is now inaccurate for the "monitor CI" clause, and the Audit trail event list omits `review-readiness`. No documented rule in this packet mandates README updates, so non-blocking; the docs-staleness is real.

No net-new abstractions introduced beyond the single `review_readiness` function the brief names.

VERDICT: APPROVE

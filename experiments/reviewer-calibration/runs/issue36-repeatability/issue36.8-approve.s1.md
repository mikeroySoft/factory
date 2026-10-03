# Review: issue #8 — CI/merge readiness for the review lane

## Spec coverage (issue #8 exit gate)

| Criterion | Evidence | Status |
|---|---|---|
| Missing checks not ready | `factory/dispatch.py:292-296` (`bool(rows)` required for `valid`); test row `("[]", 0, "ci_pending")` at `tests/test_factory.py:1392` | Met |
| Unparsable checks not ready | `factory/dispatch.py:284-291` (`json.loads` failure leaves `rows=[]`, `confirmed=False`); test rows `"not json"`, `"null"`, `'[{}]'`, `bucket:[]`, `bucket:"unknown"` at `tests/test_factory.py:1393-1399` | Met |
| Pending not ready | `factory/dispatch.py:307-310` requires all `pass`; test `tests/test_factory.py:1395,1400` | Met |
| Failed / cancelled not ready, distinct state | `factory/dispatch.py:305-306` → `ci_failed`; test `tests/test_factory.py:1401-1402` | Met |
| Approval + passing required checks for same head → ready | `factory/dispatch.py:297-299` (review matched on `head`), `:307-310` (`confirmed and valid and returncode==0 and all pass`); test `tests/test_factory.py:1405` | Met |
| New head SHA clears readiness | `factory/dispatch.py:287-289` (fresh head replaces `head`, `confirmed=False` on mismatch) + `:297-299` (review lookup keyed on new head → `review_pending`); test `tests/test_factory.py:1411-1418` covers both the mid-query push and the listed-head change | Met |
| Changes requested distinct state | `factory/dispatch.py:301-302`; test `tests/test_factory.py:1419-1421` | Met |
| No `gh pr merge` for external PR | `factory/dispatch.py:274-312` issues only `gh pr checks` / `gh pr view`; test `tests/test_factory.py:1425` plus `self.fail` on any unexpected command at `:1387` | Met |
| Events via existing `record()` | `factory/dispatch.py:311-312` | Met |
| `python -m unittest` passes | Gate report: test PASS | Met |

Fail-closed properties hold: `ready` is unreachable when `gh pr view` fails (`confirmed` stays `False`, `:287`), when `gh pr checks` exits nonzero (`:307`), or when any required check is `skipping` (`:308-309`). The docstring at `:275` and the absence of any merge path keep this advisory-only, matching the issue's out-of-scope list.

## Standards

No AGENTS.md/CONTRIBUTING.md at this head. README conventions relevant here: review lane writes journal events via `record()`, intake under the shared lock, dry runs print only. Diff complies: readiness is skipped in dry run (`factory/dispatch.py:232-233`; test `tests/test_factory.py:1422-1424`).

## Required fixes

None.

## Optional suggestions

1. **Duplicate readiness evaluation per pass for newly admitted revisions.** `factory/dispatch.py:232-233` runs `review_readiness` for every tracked PR before the admission check, and `:271` runs it again after `review_external_pr`. A newly admitted head therefore gets two `review-readiness` rows and four `gh` calls in one pass. The second call is the meaningful one (post-review); the first is redundant for that case. Guarding `:232-233` on an already-recorded `review-intake` pair, or dropping `:271` and relying on the next pass, would avoid it. Not a correctness defect.

2. **Unconditional per-pass event append.** `factory/dispatch.py:311-312` records a `review-readiness` row on every pass for every tracked PR, including PRs whose state is unchanged, and `:218-223` now keeps every ever-approved PR in the lane. On a 10-minute timer that is ~144 rows/day/PR in `events.jsonl` for the lifetime of each open PR. Emitting only when `(head, state)` differs from the last recorded row would bound growth. No documented rule requires change-only emission for legacy events, so non-blocking.

3. **README now stale.** README's review-lane paragraph ends with "This lane does not change contributor branches, monitor CI, or merge external PRs." The diff adds CI monitoring to this lane and a new `review-readiness` event name not listed in the audit-trail section. Issue "Touches" does not list README, so not a blocking omission; worth a follow-up doc line.

4. **`checks.stdout` parse order.** `factory/dispatch.py:285-286`: if `checks.stdout` is unparsable, `current` is never parsed, so a head move during the query is not reflected in the recorded `head`. Outcome is still fail-closed (`review_pending`/`ci_pending`, never `ready`), and the next pass corrects it; swapping the two `json.loads` calls would make the recorded head accurate in that case.

VERDICT: APPROVE

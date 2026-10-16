# Review: #8 — CI / merge readiness for the review lane

## Spec coverage (issue #8 exit gate)

| Criterion | Evidence | Status |
|---|---|---|
| Missing / unparsable / pending / failed / cancelled checks are not ready | `factory/dispatch.py:293-297` (`valid` requires non-empty list, dict rows, known bucket); `:303-308` (`ci_failed` on `fail`/`cancel`, `ready` needs `confirmed and valid and returncode == 0 and all pass`); `tests/test_factory.py:1390-1404` enumerates `[]`, `null`, `not json`, `[{}]`, bad bucket type, unknown bucket, pending, fail, cancel, skipping, pass with rc≠0 | Met |
| Approval + passing required checks for same head → ready | `:298-300` review lookup keyed on `head`; `:306-308`; test `:1404` (`"pass"`, rc 0 → `ready`) | Met |
| New head SHA clears readiness | `:284-286` fresh `headRefOid` replaces `head`, `confirmed=False` on mismatch; test `:1411-1417` (`fresh_head="head-2"` → `review_pending`, then after PR listing catches up still `review_pending`) | Met |
| No `gh pr merge` for external PR | No merge invocation added; `run` mock in test fails on any unexpected command (`:1386`) and asserts `:1426` | Met |
| Distinct states ci_pending / ci_failed / changes_requested / ready | `:301-308`; `changes_requested` tested `:1418-1420` | Met |
| Events via existing `record()` | `:309-310` `record("review-readiness", ...)` | Met |
| Dry run writes nothing | `:232-233`, `:268`; test `:1421-1423` | Met |

Fail-closed properties hold: a failed/unparsable `gh pr view` leaves `confirmed=False` so `ready` is unreachable (`:284-290`); a `skipping` required check blocks `ready` (`:306-308`).

## Required fixes

None.

## Optional suggestions

1. **Duplicate readiness event for a newly admitted revision.** `factory/dispatch.py:232-233` calls `review_readiness` before the intake/approval short-circuit, and `:271` calls it again after `review_external_pr`. A first-time PR therefore records `review_pending` and then the real state in the same pass, and the first row lands before the `review-intake` row. Not a correctness defect (last row wins and tests read `[-1]`), but it doubles journal noise and `gh` calls (4 per new PR). Moving the early call below the `review-intake` recorded-pair check, or only calling it in the "already recorded" branch, removes the duplicate.

2. **Parse order hides head refresh on unparsable checks.** `:283` parses `checks.stdout` before `fresh.stdout`; a `ValueError` there skips the head update, so the event records the stale `head` and the stale-head review is consulted. Readiness stays non-ready (rows invalid), so fail-closed is preserved, but the recorded `head` is misleading. Parsing `fresh` first in its own `try` fixes the evidence without changing outcomes.

3. **README now contradicts behavior.** README states the lane "does not change contributor branches, monitor CI, or merge external PRs" and the audit-trail list omits `review-readiness`. The issue's Touches list excludes README, so not blocking; worth a one-line update when the lane docs are next touched.

4. **Per-pass unconditional emission.** `:309` records a `review-readiness` row every pass even when state is unchanged. Legacy events have no change-only rule, so this is journal growth only; a compare-to-last-row guard would be a small, non-abstracting change if growth matters.

No net-new abstractions introduced; `review_readiness` is a single function matching the brief's "readiness derivation" in `dispatch.py`.

VERDICT: APPROVE

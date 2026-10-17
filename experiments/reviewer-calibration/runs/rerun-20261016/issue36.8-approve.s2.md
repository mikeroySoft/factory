## Spec (issue #8)

| Criterion | Evidence | Status |
|---|---|---|
| Distinct states: CI pending, CI failed, changes requested, ready | `factory/dispatch.py:301-310` (`review_pending`, `changes_requested`, `ci_pending`, `ci_failed`, `ready`) | Met |
| Missing/unparsable/pending/failed/cancelled → not ready | `factory/dispatch.py:293-297` validity gate; `:306-310`; `tests/test_factory.py:1391-1404` (`[]`, `not json`, `null`, `[{}]`, `pending`, `fail`, `cancel`, nonzero exit with `pass`) | Met |
| Approval + passing required checks for same head → ready | `factory/dispatch.py:298-300` review bound to `head`; `:308-310` requires `confirmed and valid and returncode==0 and all pass`; `tests/test_factory.py:1404` | Met |
| New head SHA clears readiness | `factory/dispatch.py:287-289` re-read head replaces `head` so the old APPROVE no longer matches; `tests/test_factory.py:1410-1417` (both the push-during-query race and the next-pass case) | Met |
| No `gh pr merge` for external PR | No merge invocation in diff; `tests/test_factory.py:1426` | Met |
| Events via `record()` to `.factory/events.jsonl` | `factory/dispatch.py:311-312` | Met |
| Touches limited to `dispatch.py`, tests | Diff touches only those two files | Met |
| Out of scope (no pushes/merges/dashboard) | None present | Met |
| Dry run writes nothing | `factory/dispatch.py:232-233`, `:268-271` guarded by `not dry_run`; `tests/test_factory.py:1422-1424` | Met |

Fail-closed properties verified in the diff: a failed `gh pr view` leaves `confirmed=False` (`:283`, `:287`) so `ready` is unreachable; `skipping` buckets block `ready` (`:309`); unparsable rows produce `checks=[]` in the event (`:312`).

## Standards

No AGENTS.md/CONTRIBUTING.md at this head. README conventions (`record()` journal, dry-run no-write, lane never merges external PRs) are followed.

## Required fixes

None.

## Optional suggestions

1. `README.md` "Opt-in review lane" paragraph still says "This lane does not change contributor branches, **monitor CI**, or merge external PRs." The diff now queries `gh pr checks`/`gh pr view` per pass (`factory/dispatch.py:276-282`) and emits a `review-readiness` event. Not listed in the issue's Touches, so non-blocking, but the sentence is now false; suggest amending in a follow-up alongside the audit-trail event list.
2. `factory/dispatch.py:220` keeps any PR with a past `APPROVE` in the intake loop regardless of label/review-request state, and `:232-233` then records a `review-readiness` row for every tracked open PR on every pass. Unbounded journal growth per open PR (one row per 10-minute timer tick) with no change-only suppression. Behaviourally correct for the spec; worth a change-only guard if journal size becomes a concern.
3. `factory/dispatch.py:311-312` `review_head` is always either `head` or `None` (the lookup at `:298-300` already filters on `head == head`); the field carries no information beyond `state != "review_pending"`.

VERDICT: APPROVE

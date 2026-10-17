## Spec (issue #8)

Exit-gate criteria traced to the diff:

- Missing / unparsable / empty checks → not ready: `valid` requires a non-empty list of `{name:str, bucket∈{pass,fail,pending,skipping,cancel}}` (`factory/dispatch.py:291-295`); invalid rows leave `state = "ci_pending"` (`factory/dispatch.py:302-303`). Tests: `[]`, `not json`, `null`, `[{}]`, `bucket:[]`, `bucket:"unknown"` (`tests/test_factory.py:1391-1398`).
- Pending / failed / cancelled → not ready: `fail`/`cancel` → `ci_failed` (`factory/dispatch.py:304-305`); `pending`/`skipping` never satisfy `all(bucket == "pass")` (`factory/dispatch.py:306-308`). Tests `tests/test_factory.py:1399-1402`.
- Ready only with APPROVE for the same head and all required checks passing with `gh pr checks` exit 0: `review` is filtered by `head == head` (`factory/dispatch.py:296-298`); ready additionally requires `confirmed` (fresh `headRefOid` equals the intake head) and `checks.returncode == 0` (`factory/dispatch.py:306-308`). Test `tests/test_factory.py:1403-1404` (exit 1 with all-pass → `ci_pending`, exit 0 → `ready`).
- New head SHA clears readiness: a fresh head mismatch rewrites `head` to the current SHA (`factory/dispatch.py:286-287`), so the head-bound review lookup misses → `review_pending`. Tests `tests/test_factory.py:1410-1416` (both mid-query push and next-pass head).
- No `gh pr merge` for external PRs: readiness is record-only (`factory/dispatch.py:309-310`); test asserts no merge command across all passes (`tests/test_factory.py:1425`).
- Events through existing `record()` (`factory/dispatch.py:309`), dry-run writes nothing (`factory/dispatch.py:232-233`, `tests/test_factory.py:1422-1424`).
- Distinct states `review_pending` / `changes_requested` / `ci_pending` / `ci_failed` / `ready` (`factory/dispatch.py:299-308`).

Eligibility change `verdict in {"REQUEST_CHANGES", "APPROVE"}` (`factory/dispatch.py:220`) is required so approved PRs keep being tracked after GitHub consumes the review request; without it a later head push would never re-derive readiness.

## Standards (README)

No AGENTS.md/CONTRIBUTING.md at this head. README conventions on the review-only lane (head-bound evidence, fail-closed, no merge) are respected by the code above.

Non-blocking note: README's intake section states "This lane does not change contributor branches, monitor CI, or merge external PRs." `review_readiness` (`factory/dispatch.py:275`) now queries `gh pr checks`, making that sentence stale. README is not in the issue's Touches list, so not a criterion failure; worth a one-line doc update in a follow-up.

VERDICT: APPROVE

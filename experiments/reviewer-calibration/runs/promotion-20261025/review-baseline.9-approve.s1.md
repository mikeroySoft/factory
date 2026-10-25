## Standards

No `AGENTS.md`/`CONTRIBUTING.md` in the gated revision; `docs/` holds plans, not coding rules. README conventions (`needs-review` opt-in semantics, README.md:332) are respected. Prior lane commits (agent/5–8) added no CHANGELOG entry, so none is required here.

Smell baseline (judgement calls, non-blocking):
- **Duplicated Code** — `factory/dashboard.py:841-850` re-derives `review_pending/changes_requested/ci_pending/ci_failed/ready` from `checks` buckets, duplicating `dispatch.review_readiness` (dispatch.py:300-310), which already records `state` on the same `review-readiness` row the dashboard reads (`readiness.get("state")`, dashboard.py:848). Two classifiers can drift; e.g. a head that dispatch could not confirm yields `ci_state: "pass"` but `state: "ci_pending"` in the row (dashboard.py:1141 renders "CI pending · Required CI: pass"). Consistent with dispatch today; only a maintenance risk.

## Spec (issue #9)

Acceptance criteria:
- Six states, PR number/title/author/head/last-reviewed SHA/verdict/CI state — `dashboard.py:853-858`, rendered at `dashboard.html:1134-1144`. ✔
- Link each row to GitHub — `dashboard.html:1136`. ✔
- Stalled items in existing Inbox, no mutation controls — `dashboard.html:1160-1165`, `1550-1551` (text + `reviewRow`, no buttons). ✔
- Snapshot test covers all six states, PR links, SHA-specific data — `tests/test_factory.py:2778-2840` (states dict at 2826-2828; URL/head per PR 2830-2833; stale-head `review_head`/`verdict`/`ci_state: unknown` 2835-2837). ✔
- README operating description — `README.md:349-365`. ✔
- Out of scope respected: no review submission, branch edits, merge, or separate service. ✔

No missing or wrong requirements found.

## Plan drift

Plan status not recorded; nothing to diff against. Files changed match the issue's "Touches" list exactly (README.md, factory/dashboard.py, factory/dashboard.html, tests/test_factory.py). The gate report shows no evidence of the exit-gate's manual "running `factory dashboard` displays the queue" run; the test's `EVENTS.read_bytes().startswith(before)` check (tests/test_factory.py:2823) covers the "without changing … state" half for the events journal only. Not a required defect.

## Required fixes

None.

## Optional suggestions

1. `dashboard.html:1162` — `review_pending` with a stale `APPROVE` on an old head is pushed into the "waiting on you" count. Dispatch re-reviews new heads automatically (dispatch.py:264-271), so this is not human-actionable; consider surfacing only when `pr.reason` is set.
2. `dashboard.py:150` — queue is bounded by the existing `pullRequests(first:100)` (all states, newest-first). Opted-in open PRs older than the 100 most recent PRs of any state silently drop from the queue, while dispatch intake uses `--state open --limit 1000` (dispatch.py:203-206). Matches the dashboard's existing 100-item bound convention; document or add `states:[OPEN]` if the queue is meant to be exhaustive.
3. `dashboard.html:1136` — other inbox links route through `safeSourceURL` (dashboard.html:1493); `pr.url` is GitHub-generated so risk is nil, but using the same helper keeps one convention.
4. `dashboard.py:841-850` — consider reading `readiness["state"]` directly instead of re-classifying (see Duplicated Code above).

VERDICT: APPROVE

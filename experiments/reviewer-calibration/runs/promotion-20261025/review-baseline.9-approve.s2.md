Note: `./review.diff` and `./snapshot` were absent from the sandbox; the cwd is the gated revision (`45d96f6`, detached) with base `311ba91` (agent/8 commit). I reconstructed the diff via `git diff 311ba91..HEAD` (4 files, +176/−5) and read the checked-out tree as the snapshot. Line cites below are into that diff.

## Standards

No `AGENTS.md`/`CONTRIBUTING.md` in repo; only documented conventions are the issue template (`.github/ISSUE_TEMPLATE/agent_task.md`) and `[tool.ruff] line-length = 100` (`pyproject.toml:37`, not run in `ci.yml`; `dashboard.py` already has 28 lines >100).

- No hard violations.
- Smell check: `review_queue()` (dashboard.py:153-201) mirrors the state cascade in `dispatch.review_readiness` (dispatch.py:300-310) — Duplicated Code judgement call, but the dashboard must classify from recorded evidence without `gh` calls, so inlining is the simpler choice. No speculative generality; no new abstractions beyond one pure function and three small JS helpers.

## Spec (issue #9)

| Criterion | Evidence | Status |
|---|---|---|
| Six states: pending review / changes requested / CI pending / CI failed / ready / escalated | dashboard.py:185-193; `REVIEW_STATES` dashboard.html:58 | ✓ |
| Row fields: local PR number, title, author, head SHA, last reviewed SHA, verdict, CI state | dashboard.py:194-200; dashboard.html:62-71 | ✓ |
| Link each row to GitHub | dashboard.html:64 (`href: pr.url`) | ✓ |
| Stalled items in existing inbox, no mutation controls | dashboard.html:84-90 pushes into `inboxItems()`; :115-116 renders `reviewRow` + text only, no form/buttons | ✓ |
| Opted-in = `needs-review` label, review request to viewer, or previously admitted — matches dispatch intake (dispatch.py:211-223) | dashboard.py:165-174; GraphQL `viewer{login}`, `reviewRequests` :136,:145 | ✓ |
| Old approval never makes new head ready | state derives from `current` (head-matched, :176-177), display from `review` (latest any-head, :164) | ✓ |
| Snapshot test covers six states, PR links, SHA-specific data | test_factory.py:286-298 (states, url, head, review_head old vs current, ci_state) | ✓ |
| No mutation of labels/reviews/branches/merge | test asserts `events.jsonl` unchanged :276,:284; `review_queue` is pure; only remote call is read-only GraphQL | ✓ |
| Out of scope untouched (no review submission, no separate service) | no new endpoints/handlers in diff | ✓ |
| README documents the lane | README.md:14-26 | ✓ |

Cross-checked field contracts against the snapshot: `review-readiness` rows carry `head/state/checks[].bucket/at` (dispatch.py:311-312, :408); `review-result` carries `head/verdict` (dispatch.py:363); PR escalations carry `pr=`/`reason` while ticket escalations use `ticket=` (dispatch.py:260, :555, manage.py:432) — so `history` keyed by `row["pr"]` (dashboard.py:157) cannot pick up ticket escalations. `h()` supports `style` objects (snapshot dashboard.html:464); `initFromHash` regex `[\w-]+` accepts `review-N` keys (snapshot :1691); `INBOX_KINDS[i.kind]` is only indexed for non-WAITING items (:1559) and `external-review` is in `WAITING` (:76).

## Plan drift

- Plan: not recorded. Files changed (README.md, factory/dashboard.html, factory/dashboard.py, tests/test_factory.py) are exactly the four the issue's **Touches** names. No unnamed files.
- Gate report has `test: PASS` (covers `python -m unittest`). The manual exit-gate proof ("running `factory dashboard` displays the queue") is not in the gate report; the read-only property is evidenced by the test's `events.jsonl` assertion and the absence of any write path in the diff. Not a defect.

## Required fixes

None.

## Optional suggestions

1. **dashboard.py:199 / dashboard.html:86 — dead `reason` path, and permanently-skipped reviews never reach the Inbox.** `review-result` events never carry `reason` (dispatch.py:363), so `current.get("reason")` is always `None` and the `pr.state === 'review_pending' && pr.reason` branch is unreachable. Reviews dropped for `prompt_too_large`/`unparsed_verdict`/`review_exit` are recorded only on lifecycle executions (dispatch.py:336-356) and are "not automatically retried" (README.md:8), yet they render as plain "Pending review" and are never surfaced as stalled. README.md:23-24 ("pending reviews needing human attention also appear in Inbox") currently only holds for the old-approval/new-head case. If those count as "actionable stalled items", derive them from `executions` already in the snapshot. Not blocking: the issue doesn't define "stalled" and the six-state exit gate is met.
2. dashboard.html:64 uses raw `pr.url` with `rel: 'noopener'`; the sibling system briefing uses `safeSourceURL(...)` + `'noopener noreferrer'` (snapshot dashboard.html:1493). Consistency only; `pr.url` is API-sourced.
3. Ruff `line-length = 100` exceeded at dashboard.py:167 (107), :171 (103), test_factory.py:275 (104). Not CI-enforced and the files already violate it widely.

VERDICT: APPROVE

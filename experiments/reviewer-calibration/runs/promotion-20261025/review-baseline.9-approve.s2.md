## Review — issue #9 external PR review queue

Scope check: issue "Touches" names `factory/dashboard.py`, `factory/dashboard.html`, `tests/test_factory.py`, `README.md`. Diff touches exactly those four files (`review.diff:1,30,120,230`). No plan recorded; no file drift.

### Spec (issue #9 body + agent brief)

| Criterion | Evidence | Status |
|---|---|---|
| Six states on opted-in PRs | `review.diff:185-193` (review_pending / changes_requested / ci_pending / ci_failed / ready / escalated) | met |
| Row fields: number, title, author, head SHA, last reviewed SHA, verdict, CI state | `review.diff:194-200`; rendered `review.diff:62-70` | met |
| GitHub link per row | `review.diff:64` (`href: pr.url`) | met |
| Stalled items in existing Inbox, no mutation controls | `review.diff:84-90` push into `inboxItems`; `review.diff:115-116` renders read-only `reviewRow` + GitHub-only guidance; `WAITING` extended `review.diff:76` | met |
| Snapshot test covers six states, links, SHA-specific data | `review.diff:286-299` asserts all six states, `url`, `head`, `review_head`/`ci_state` divergence for stale head (PR 1) | met |
| No mutation of labels/reviews/branches/merge | `review.diff:276,284` asserts events.jsonl unchanged; only new GitHub I/O is GraphQL fields `review.diff:136,144-145` | met (test); live `factory dashboard` run not in gate report |
| Opt-in = `needs-review` label, review request to viewer, or prior admitted review-result | `review.diff:165-174` mirrors `dispatch.review_intake_pass` (`snapshot/factory/dispatch.py:211-223`) | consistent |
| SHA-specific CI from `review-readiness` (no new poll) | `review.diff:178-179` filters on `head`; matches `dispatch.review_readiness` recorder (`snapshot/factory/dispatch.py:311`) | consistent |

Inbox integration checked against existing consumers: sort tiebreaker tolerates missing `t` (`dashboard.html:1189`), `informational` list excludes WAITING kinds so `INBOX_KINDS[i.kind]`/`i.t.number` at `dashboard.html:1559` is never reached for review items, hash regex `[\w-]+` accepts `review-<n>` keys (`dashboard.html:1691`), briefing microtask guarded by `item?.t` (`dashboard.html:1560`).

### Standards

No AGENTS.md / CONTRIBUTING.md in snapshot. `pyproject.toml:36-37` sets ruff `line-length = 100`; not enforced by `.factory.toml` gate or `ci.yml` (unittest only), and `dashboard.py` already has 28 lines >100. Test follows the existing `mock.patch.dict(dashboard.__dict__)` + `dashboard.configure` pattern (`tests/test_factory.py:596-597`).

### Plan drift

- Files outside plan: none.
- Planned proof missing from gate report: exit-gate item "Running `factory dashboard` displays the queue without changing labels, reviews, branches, or merge state" is not evidenced by the gate (only `test: PASS`). The read-only property is covered by the new test's events-journal assertion and by the diff containing no write calls, so this does not expose a defect.

### Required fixes

None.

### Optional suggestions

1. `review.diff:167` is 104 chars, over the `[tool.ruff] line-length = 100` in `pyproject.toml:37`. Not gate-enforced and pre-existing violations exist; wrap if convenient.
2. `CHANGELOG.md:3-13` has an `## Unreleased` section where comparable dashboard features (#79, #67) are logged; this diff adds a new snapshot key `review_queue` (`review.diff:226`) without an entry. Issue "Touches" does not name CHANGELOG, so non-blocking.
3. `review.diff:199` `current.get("reason")`: `review-result` events are recorded without a `reason` field (`snapshot/factory/dispatch.py:363`), so the `review_pending && pr.reason` inbox branch at `review.diff:86` is effectively only reachable via escalation (already covered by the `escalated` branch). Dead condition; harmless.
4. `review.diff:183-184` buckets `{pass, skipping}` → `ci_state: "pending"`. Dispatch likewise withholds `ready` for skipped checks (`dispatch.py:307-309`), so the state is consistent, but the label "pending" is slightly misleading for skipped required checks. Cosmetic.

VERDICT: APPROVE

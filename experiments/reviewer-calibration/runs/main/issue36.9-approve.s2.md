# Review: issue #9 — external PR review queue

## Spec coverage

| Criterion | Evidence |
|---|---|
| Six states classified from SHA-bound evidence | `factory/dashboard.py:834-852` — review/readiness matched on `head`; `ci_pending`/`ci_failed`/`ready` only after a current-head `APPROVE`; stale approval leaves `review_pending` |
| Row fields: number, title, author, head, last reviewed SHA, verdict, CI state | `factory/dashboard.py:853-859`; rendered `factory/dashboard.html:1134-1143` |
| GitHub link per row | `factory/dashboard.html:1136` (`href: pr.url`, `rel: 'noopener'`) |
| Stalled items in existing inbox, no mutation controls | `factory/dashboard.html:1160-1165`, `1551-1552` — briefing is `reviewRow` + text; no action buttons |
| Opted-in PRs incl. consumed review requests | `factory/dashboard.py:828-832` (label ∨ viewer request ∨ prior review-result) |
| Snapshot test: six states, links, SHA-specific data | `tests/test_factory.py:2825-2841` — exact dict equality also proves #7/#8/#9 exclusion; `queue[1]` asserts `review_head="old-head"`, `verdict="APPROVE"`, `ci_state="unknown"` |
| README operating description | `README.md:349-365` |
| Unittest passes | gate `test: PASS` |

Draft/closed exclusion, Team review requests (`... on User` → `{}` → not matched, `factory/dashboard.py:825-826`), and GitHub-failure fallback (`review_prs=[]`, `factory/dashboard.py:869-876`) are handled.

## Required fixes

None.

## Optional suggestions

1. `factory/dashboard.py:838` — `escalation` is the last `escalate` row for the PR regardless of `head`, while review/readiness are head-bound. A PR escalated on an old head stays `escalated` after a new push, and `reason` (`:858`) reports the old head's reason. Arguably correct (the human issue is still open), but inconsistent with the SHA-specific model the README advertises; consider binding to `head` or documenting it.
2. `factory/dashboard.html:1149` — `INBOX_KINDS` has no `external-review` entry while `WAITING` (`:1148`) does. `[INFERENCE]` Any existing consumer that labels items via `INBOX_KINDS[i.kind]` (e.g. the informational list below the briefing) will print `undefined` for review items. Not visible in the diff; verify once in the live UI.
3. `tests/test_factory.py:2824` — `read_bytes().startswith(before)` permits appends, so it doesn't prove the snapshot wrote nothing. If the intent is "no mutation", assert equality (the mocked `dispatcher`/`upstream_state` should make reconciliation a no-op); if appends from lifecycle reconciliation are expected, a comment saying so would prevent the next reader from tightening it incorrectly.
4. `factory/dashboard.py:841-842` — `buckets == {"pass"}` means any non-`pass`/`fail`/`cancel` bucket (e.g. a skipped check) yields `pending` indefinitely. Depends on dispatch's bucket vocabulary, which is outside this diff.
5. `factory/dashboard.py:37` — `LABEL_REVIEW` breaks the alphabetical import order of the block. Style only.

No net-new abstractions beyond the brief: `review_queue` is a single pure projection and the HTML adds three small functions used by both the panel and the inbox.

VERDICT: APPROVE

## Spec (issue #9)

| Criterion | Evidence | Status |
|---|---|---|
| Six states classified from head/review/CI evidence | `factory/dashboard.py:843-851` — `review_pending` default, `changes_requested`/`ci_pending`/`ci_failed`/`ready` from head-bound `current` verdict + `readiness`, `escalated` override | met |
| Row fields: number, title, author, head SHA, last reviewed SHA, verdict, CI state | `factory/dashboard.py:852-857`; rendered at `factory/dashboard.html:1134-1141` | met |
| Link each row to GitHub | `factory/dashboard.html:1136` (`h('a', { href: pr.url, target: '_blank', rel: 'noopener' }…)`) | met |
| Stalled items in existing inbox, no mutation controls | `factory/dashboard.html:1160-1165` pushes `changes_requested`/`ci_failed`/`escalated` and stale-approval `review_pending`; `factory/dashboard.html:1550-1551` renders `reviewRow` + text only, no buttons/`/api/act` | met |
| Snapshot test covers all six states, PR links, SHA-specific review data | `tests/test_factory.py:2778` — asserts `{1:review_pending … 6:escalated}`, per-PR `url`/`head`/`author`/`title`, `queue[1].review_head == "old-head"` with `ci_state == "unknown"` (old approval does not carry to new head), `queue[5].review_head == "head-5"`, `ci_state == "pass"` | met |
| No mutation of labels/reviews/branches/merge state | `review_queue` is a pure projection (`factory/dashboard.py:811-859`); GraphQL additions are read fields (`factory/dashboard.py:124,154-155`); test asserts `EVENTS` unchanged (`tests/test_factory.py:2817`) | met |
| README operating description | `README.md:349-365` | met |
| Out of scope respected | No review submission, branch edits, merge, or new service in diff | met |

## Standards (README conventions)

- Read-only dashboard contract and "CI evidence comes from dispatch's `review-readiness` events, not a new CI poll": honored — CI derived from `review-readiness` rows only (`factory/dashboard.py:836-842`).
- Opt-in semantics match README (label `needs-review`, viewer review request, or prior recorded review): `factory/dashboard.py:823-832`.
- Team review requests yield `requestedReviewer: {}` from `... on User`; guarded by `(r.get("requestedReviewer") or {})` at `factory/dashboard.py:825`.

## Non-blocking observation

- `escalation` lookup is not head-bound (`factory/dashboard.py:838`), unlike `current`/`readiness` (`:834-837`). A PR escalated on an old head stays `escalated` after a new push even if a fresh `review-result` exists for the new head. Issue text defines no precedence between escalation and later review, and a human issue is already open, so this is a design choice, not an acceptance-criteria failure.

VERDICT: APPROVE

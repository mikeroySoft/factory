## Standards

No `AGENTS.md`/`CONTRIBUTING.md` in repo; `docs/` holds plans, not coding rules. README conventions checked (label scheme, "Operating it", snapshot docs). No documented-standard breaches.

Smell baseline (judgement calls, none blocking):
- **Duplicated Code (suppressed):** `review_queue` (`factory/dashboard.py:839-852`) re-derives state from the same inputs as `dispatch.review_readiness` (`dispatch.py:297-312`). It intentionally recomputes CI from `checks` buckets rather than trusting `readiness.state` alone, and gates `ready` on both — consistent with dispatch's `confirmed` rule. Acceptable: the dashboard is read-only and must classify heads dispatch hasn't observed yet (→ `unknown`).
- `reviewRow` is reused for both the Ops queue and the Inbox briefing (`dashboard.html:1136-1146`, `1550-1551`) — no second rendering path. Good.

## Spec

Issue #9 scope vs diff:

| Criterion | Evidence | Status |
|---|---|---|
| Each opted-in PR with number, title, author, head SHA, last reviewed SHA, verdict, CI state | `dashboard.py:853-858` emits all fields; `dashboard.html:1136-1146` renders them | ✓ |
| Six states | `dashboard.py:843-852` → `review_pending/changes_requested/ci_pending/ci_failed/ready/escalated`; labels at `dashboard.html:1130` | ✓ |
| Opt-in semantics match dispatch intake (label, review-request to viewer, or consumed request with prior verdict) | `dashboard.py:823-832` mirrors `dispatch.py:211-223` | ✓ |
| Old approval never readies a new head | `current` is head-scoped (`dashboard.py:834-835`); test `queue[1]` → `review_pending` with `review_head="old-head"` (`tests/test_factory.py:2832-2835`) | ✓ |
| Link each row to GitHub | `dashboard.html:1138` `<a href=pr.url target=_blank rel=noopener>`; test asserts URLs (`tests/test_factory.py:2827`) | ✓ |
| Stalled items in existing inbox, no mutation controls | `dashboard.html:1160-1166` adds `external-review` kind; briefing at `1550-1551` is text + `reviewRow` only, no buttons | ✓ |
| Snapshot test covers all six states, PR links, SHA-specific data | `tests/test_factory.py:2778-2840`; also asserts `events.jsonl` unchanged (`2822`) | ✓ |
| README documents review lane | `README.md:349-365` | ✓ |
| Out of scope respected (no review submission, branch fixes, merges, separate service) | Panel is in existing `#ops` `<main>` (`dashboard.html:330-333`); no new endpoints | ✓ |

Correctness checks on the new JS: `h()` accepts `style` objects and drops `null` kids (`dashboard.html:464,455`), so `reviewRow`'s conditional `pr.reason` paragraph and inline `overflowWrap` are safe. `#inbox/review-N` round-trips through `initFromHash`'s `[\w-]+` (`dashboard.html:1691`). Review items are always in `WAITING`, so the `informational` list's `i.t.number` (`1559`) never sees one; `1560` already guards `item?.t`. Inclusion of `review_pending` + old `APPROVE` in the inbox is correct: dispatch stops re-reviewing after any APPROVE (`dispatch.py:234-236`), so a pushed-after-approval PR genuinely stalls.

## Required fixes

None.

## Optional suggestions

1. `factory/dashboard.py:857` — `current.get("reason")` is always `None`: `review-result` rows carry `findings`, not `reason` (`dispatch.py:363`). Either drop the fallback or surface `findings`; as written the `pr.reason ||` branch at `dashboard.html:1162` only fires for escalations.
2. `factory/dashboard.py:150` — queue is bounded by the existing `pullRequests(first:100, CREATED_AT DESC)`; an opted-in PR older than the 100 most recent silently disappears. Worth one line in README if that ceiling matters.
3. `CHANGELOG.md:3` — `Unreleased` not updated; sibling features list entries with issue refs. No documented rule, so non-blocking.

VERDICT: APPROVE

# Review: issue #9 — external PR review queue

## Spec coverage

| Acceptance criterion | Evidence | Status |
|---|---|---|
| Six states classified from PR number/title/author/head/last-reviewed SHA/verdict/CI | `factory/dashboard.py:811-859` (`review_queue`), state assignment `:843-851` | Met |
| Rows link to GitHub | `factory/dashboard.html:1136` (`h('a', { href: pr.url, target: '_blank', rel: 'noopener' }…)`); `url` emitted at `dashboard.py:854` | Met |
| Stalled items in existing inbox, no mutation controls | `factory/dashboard.html:1160-1166` pushes `external-review` items; briefing at `:1551-1552` renders `reviewRow` + explanatory text only, no `/api/act` wiring | Met |
| Test covers all six states, PR links, SHA-specific data | `tests/test_factory.py:2778-2840`: state map assertion `:2827-2829`, URL/head per PR `:2831-2832`, old-head approval → `review_pending` with `review_head="old-head"`, `ci_state="unknown"` `:2835-2837` | Met |
| No mutation of labels/reviews/branches/merge state | Snapshot path only reads GraphQL + `rows`; test asserts journal unchanged `tests/test_factory.py:2826` | Met |
| README documents the lane | `README.md:349-365` | Met |
| Touches limited to listed files | diff touches exactly the four listed files | Met |

Exclusion logic (`dashboard.py:825-832`) admits by `needs-review` label, pending review request to the viewer, or prior recorded verdict (so consumed review requests keep admitted PRs — matches README `:351`). Test exercises other-reviewer request, closed, and draft exclusion via the exact-equality state map at `tests/test_factory.py:2827`.

Standards: no AGENTS.md/CONTRIBUTING.md at this head. README `:364-365` ("Viewing or refreshing the queue does not change labels, reviews, branches, or merge state") is satisfied by the read-only projection. No net-new abstractions beyond the brief: `review_queue` is a single function; `REVIEW_STATES`/`reviewRow`/`reviewMatches` are the minimum for render + search.

## Required fixes

None.

## Optional suggestions

1. `factory/dashboard.py:847` — an APPROVE on the current head with no `review-readiness` evidence yields `state: "ci_pending"` while `ci_state: "unknown"`. README `:354` says missing evidence is unknown; the row text will read "CI pending … Required CI: unknown · Not observed for this head". Consistent with the six-state contract (no seventh state), but consider rendering the unknown case as `review_pending` with the advisory reason, or documenting that CI pending covers "no evidence yet". Not blocking: no criterion fixes this mapping.
2. `factory/dashboard.py:837` — `escalation` is not head-bound, unlike `current`/`readiness` (`:833-836`). A contributor pushing a new head after an escalation keeps the row `escalated` indefinitely. [INFERENCE] whether that is intended depends on dispatch's escalation semantics (README `:347-348` says exhausted attempts escalate to a human issue, which is plausibly terminal). If re-review of a new head is possible, bind escalation to `head` as well.
3. `factory/dashboard.html:1149` — `WAITING` gains `external-review`, but `INBOX_KINDS` on `:1150` has no `external-review` entry. If any existing inbox code labels items by `INBOX_KINDS[i.kind]` (outside this diff), the kind label renders `undefined`. Add `'external-review': 'External review'` if so.
4. `tests/test_factory.py:2797-2800` — `{**prs[0], …}` shallow-copies and shares `labels`/`reviewRequests` dicts with PRs 1/2. Harmless here since no later mutation touches the shared dicts for 7–9, but a future edit to `prs[0]["labels"]["nodes"]` after `:2797` would silently change PR 7. Use `copy.deepcopy` or construct fresh dicts.

VERDICT: APPROVE

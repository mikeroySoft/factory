# Review: issue #9 — external PR review queue

## Spec

| Criterion | Evidence |
|---|---|
| Per-PR number, title, author, head SHA, last reviewed SHA, verdict, CI state, one of six states | `factory/dashboard.py:854-860` builds exactly those fields; `state` ∈ `{review_pending, changes_requested, ci_pending, ci_failed, ready, escalated}` (`:843-851`) |
| Old approval never readies a new head | `current` is head-bound (`:833-834`), while `review`/`review_head` is the latest regardless of head (`:822`); test pins PR 1 → `review_pending` with `review_head == "old-head"` (`tests/test_factory.py:2832-2835`) |
| Opt-in = label, viewer review request, or prior recorded verdict (survives GitHub consuming the request) | `:824-832`; test covers consumed-request PR 5 (`tests/test_factory.py:2814-2815`) and non-viewer request PR 7 excluded (`:2795-2796`) |
| Rows link to GitHub | `factory/dashboard.html:1136`; asserted `tests/test_factory.py:2829` |
| Stalled items in inbox, no mutation controls | `factory/dashboard.html:1160-1166` pushes items with `url`-less, text-only briefing (`:1551-1552`); no `onclick`/`/api/act` wiring |
| No label/review/branch/merge mutation | Snapshot is pure projection over `rows`; test asserts journal unchanged across `snapshot()` (`tests/test_factory.py:2823`) |
| Test covers all six states + SHA-specific data | `tests/test_factory.py:2825-2836` |
| README documents the lane | `README.md:349-365` |

Out-of-scope items (submitting reviews, fixing branches, merging, separate service) not touched.

## Standards

No AGENTS.md/CONTRIBUTING.md at this head. README conventions satisfied: CI evidence sourced from `review-readiness` events rather than a new poll (`factory/dashboard.py:835-836`, README `:353-354`); GraphQL additions stay within the existing single query (`:124`, `:154-155`).

## Non-blocking observations

- `escalated` is not head-bound (`factory/dashboard.py:837`): a later push after an `escalate` event keeps the PR in `escalated`. Issue text does not specify when escalation clears, so not an acceptance failure; worth confirming intended.
- `INBOX_KINDS` (`factory/dashboard.html:1149`) gains no `external-review` entry while `WAITING` does (`:1148`). `[INFERENCE]` If any existing renderer indexes `INBOX_KINDS[i.kind]` for the badge/heading, external-review items will show `undefined`; not verifiable from the diff.
- APPROVE with `ci == "unknown"` renders as `ci_pending` (`factory/dashboard.py:847`); row text still shows `Required CI: unknown` (`factory/dashboard.html:1141`), so the distinction remains visible.

VERDICT: APPROVE

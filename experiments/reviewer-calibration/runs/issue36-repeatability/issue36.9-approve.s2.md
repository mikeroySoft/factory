# Review: issue #9 — external PR review queue

## Spec coverage

| Criterion | Evidence |
|---|---|
| Six states classified from SHA-bound evidence | `factory/dashboard.py:834-851`; test asserts all six `tests/test_factory.py:2826-2828` |
| Number, title, author, head, last reviewed SHA, verdict, CI state | `factory/dashboard.py:852-857`; rendered `factory/dashboard.html:1136-1141` |
| GitHub link per row | `factory/dashboard.html:1136` (`href: pr.url`, `rel: 'noopener'`); asserted `tests/test_factory.py:2830` |
| Old approval does not make new head ready | `current` is head-filtered `factory/dashboard.py:834-835`; PR 1 case `tests/test_factory.py:2802-2804, 2834-2836` |
| Inbox items, no mutation controls | `factory/dashboard.html:1160-1165`, briefing is `reviewRow` + text only `factory/dashboard.html:1550-1551` |
| No label/review/branch/merge mutation | GraphQL additions are reads `factory/dashboard.py:124,154-155`; journal append-only check `tests/test_factory.py:2817,2824` |
| README documents the lane | `README.md:349-365` |
| `python -m unittest` | gate: test PASS |

Opt-in retention after GitHub consumes a review request (`factory/dashboard.py:828-832`, test PR 5 `tests/test_factory.py:2815-2816`) and exclusion of other-reviewer/closed/draft PRs (`tests/test_factory.py:2793-2796`, implied by exact-dict assertion at `:2826`) are covered.

## Required fixes

None.

## Optional suggestions

1. `factory/dashboard.py:838,850-851` — `escalation` is not head-bound, unlike `current`/`readiness`. Trigger: dispatch escalates head X, contributor pushes head Y, dispatch re-admits and approves Y with green CI → queue still shows **escalated**, masking **ready**. Spec does not define escalation persistence, so not blocking; if escalation is meant to clear on a new head, filter on `e.get("head") == head` like line 835.
2. `factory/dashboard.py:846-847` — APPROVE with `ci == "unknown"` (no `review-readiness` row for this head) yields `ci_pending`, while the row shows `Required CI: unknown`. README `:354-355` says missing evidence is unknown; mapping unknown → "CI pending" is a defensible six-state choice, but worth a comment.
3. `factory/dashboard.html:1148` — `INBOX_KINDS` has no `'external-review'` entry while `WAITING` (`:1147`) gains it. `[INFERENCE]` If any existing renderer does `INBOX_KINDS[item.kind]`, review items print `undefined`. Not verifiable from the diff.
4. `factory/dashboard.html:1138-1139` — `h('code', { style: { overflowWrap: 'anywhere' } })` assumes `h` accepts style objects. `[INFERENCE]` If `h` uses `setAttribute`, this becomes `style="[object Object]"`. Not verifiable from the diff.
5. `factory/dashboard.py:37` — `LABEL_REVIEW` breaks the alphabetical import order of the block. Style only.

No net-new abstractions beyond the brief; `review_queue` is the snapshot projection the issue names.

VERDICT: APPROVE

## Standards

Sources checked: `README.md` (no coding-standards section), `docs/*.md` (plans, no code rules), no `CONTRIBUTING.md` in repo. The repo documents nothing the diff violates. Smell baseline (judgement calls only):

- **Duplicated Code** — `factory/dashboard.py:843-851` re-derives review_pending / changes_requested / ci_pending / ci_failed / ready from `review-result` + check buckets; `factory/dispatch.py:300-312` already computes and records the same cascade as `review-readiness.state` bound to the same head (readiness is always recorded after a result, dispatch.py:233,271). `state = readiness.get("state", "review_pending")` plus the escalation override would be smaller and cannot drift from dispatch. Dashboard-side recomputation does cover the window where a `review-result` exists at the head but no `review-readiness` yet (crash between 363 and 271); keep if that window matters.
- **Dead branch** — `factory/dashboard.py:857` `current.get("reason")`: `review-result` rows carry `pr, head, verdict, findings` (dispatch.py:363), never `reason`, so `reason` is non-null only when `escalation` is set, i.e. `state == "escalated"`. Consequently `factory/dashboard.html:1162` `pr.state === 'review_pending' && pr.reason` is unreachable; only `verdict === 'APPROVE'` selects stale-approval rows.
- `factory/dashboard.py:841-842`: `{"pass","skipping"}` → `"pending"`. Dispatch's `ready` also rejects skipping (dispatch.py:307-309), so state agrees, but the displayed `Required CI: pending` for a skipped required check is mislabelled. Cosmetic.
- `tests/test_factory.py:2824` `startswith(before)` proves only that the journal was not truncated/rewritten; `assertEqual` would prove the read-only claim the test is named for.

No CHANGELOG entry (`CHANGELOG.md:3` has an Unreleased section). Not a documented rule; the four preceding review-lane commits (#5–#8) also skipped it. Optional.

## Spec (issue #9 + agent brief; no approved scope changes in comments)

| Criterion | Evidence | Status |
|---|---|---|
| Opted-in PRs (label, review request to viewer, consumed request) | `dashboard.py:823-832`; mirrors `dispatch.py:211-223` | ✓ |
| number, title, author, head, last reviewed SHA, verdict, CI state | `dashboard.py:852-857`; rendered `dashboard.html:1136-1141` | ✓ |
| Six states | `dashboard.py:843-851`; labels `dashboard.html:1131` | ✓ |
| Link each row to GitHub | `dashboard.html:1136` `href: pr.url` | ✓ |
| Stalled items in existing inbox, no mutation controls | `dashboard.html:1160-1166` adds items; `1550-1551` renders `reviewRow` + text only, no form | ✓ |
| Snapshot test covers all six states, links, SHA-specific data | `tests/test_factory.py:2825-2838` (states 1–6; `url`, `head`; PR1 `review_head="old-head"` vs `head="head-1"`, `ci_state="unknown"`) | ✓ |
| No mutation of labels/reviews/branches/merge | `review_queue` is pure over GraphQL + journal rows; `dashboard.py:1016` | ✓ |
| README documents the lane | `README.md:349-365` | ✓ |
| Out of scope (submit reviews, fix branches, merge, separate service) | nothing added | ✓ |

Spec judgement calls (non-blocking):
- "Actionable stalled items": a revision whose review recorded `unknown` (`prompt_too_large` / `unparsed_verdict`) is never retried (`dispatch.py:264-266`, README.md:348) yet stays `review_pending` with no inbox item — `review-intake` events are not consulted (`dashboard.py:821-838`). Whether that is "stalled" per the issue is unspecified; flagging, not requiring.
- Stale-approval rows (`verdict === 'APPROVE'`, `dashboard.html:1162`) are correctly inbox-worthy: dispatch never re-reviews after any APPROVE (`dispatch.py:234-236`).

## Changed contracts and their consumers

- `GRAPHQL` (`dashboard.py:124,154-155`): sole consumer `github()` at `dashboard.py:208`; `feedback.collect` passes its own fixed queries (`dashboard.py:947-948`). Holds. New fields are read defensively (`data.get("viewer")` 876, `pr.get("author")` 853, `pr.get("reviewRequests")` 826).
- `snapshot()` return (`dashboard.py:1016`, new key `review_queue`): consumers `_cache` 1029, `--json` 1319, HTML `S.snap.review_queue` 1145/1160. Additive; holds.
- `WAITING` (`dashboard.html:1148`, adds `external-review`): consumers `inboxCount` 1191, `renderInbox` 1534/1557. Review items are never routed to the informational branch (1559, which assumes `i.t`). Holds.
- `inboxItems()` item shape (new `review` field, no `t`): `selectedItem` 1193 finds by key; sort 1189 uses `b.t?.number`; `renderInbox` 1550 branches on `item.review` before `item.t`; `decisionForm`/`renderTaskBriefing`/`ensureBriefing` (1389, 1498, 1560) only reached via `item.t`. Holds.
- `LABEL_REVIEW` import (`dashboard.py:37`): same opt-in label dispatch uses (`dispatch.py:211`). Holds.

## Required fixes

None.

## Optional suggestions

1. `dashboard.py:843-851` — trust `readiness["state"]` instead of re-deriving the cascade; delete `ci`/`buckets` or keep only for the `ci_state` field.
2. `dashboard.py:857` / `dashboard.html:1162` — drop `current.get("reason")` and the `pr.reason` inbox clause; both are dead.
3. `tests/test_factory.py:2824` — `assertEqual(dispatch.EVENTS.read_bytes(), before)`.
4. Consider surfacing `review-intake`-without-result revisions (unknown reviewer outcome) in the inbox, or document that they are not.
5. `CHANGELOG.md:3` Unreleased entry for #9.

VERDICT: APPROVE

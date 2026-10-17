## Review: `b1defa4` Track fail-closed readiness for external PR reviews

Issue source: github.com/mikeroySoft/factory/issues/8 (public page; `gh` is unauthenticated in this sandbox). Comments contain only the triage/agent brief; no approved scope changes.

### Spec (issue #8 exit gate)

| Criterion | Evidence | Holds |
|---|---|---|
| Missing checks not ready | `valid` requires `bool(rows)` → `[]` stays `ci_pending` (dispatch.py:292, :304); test row `("[]", 0, "ci_pending")` tests/test_factory.py:1399 | ✓ |
| Unparsable not ready | `json.loads` failure swallowed, `valid` False (dispatch.py:284-291); tests :1404, :1405, :1406 | ✓ |
| Pending / failed / cancelled not ready | bucket whitelist :294; `fail`/`cancel` → `ci_failed` :305-306; `pending`/`skipping` stay `ci_pending`; tests :1402-1410 | ✓ |
| Approval + all required checks pass for same head → ready | `review` matched on `head` :297-299, `confirmed` from fresh `headRefOid` :287-288, `checks.returncode == 0` and all `pass` :307-310; test :1412 | ✓ |
| New head clears readiness | review lookup keyed on fresh head → `review_pending` (:297-300); tests :1413-1419 cover both the race (listing stale, view fresh) and the settled case | ✓ |
| No `gh pr merge` for external PR | asserted over all commands :1428; no merge call in `review_readiness`/intake | ✓ |
| Distinct states pending/failed/changes_requested/ready | :300-310 | ✓ |
| Through existing `record()` | :311 | ✓ |
| Dry run writes nothing / no gh calls | :232-233 guard; test :1423-1425 | ✓ |

Fail-closed ordering is sound: checks are fetched *before* the fresh-head read (:276-282), so a push between the two calls makes `confirmed` False; a push before both yields `headRefOid != head` → not ready.

### Changed behaviour and consumers outside the diff

- **Eligibility filter now retains APPROVE-reviewed PRs** (dispatch.py:220). Needed: review requests are consumed on submission, so an approved PR must stay tracked to ever reach `ready`. Consumers: `manage.py` does not read `review-result`; dashboard/stats/learn/evidence filter on `type(row.get("ticket")) is int` (dashboard.py:457, learn.py:34, stats.py:60), so the new ticketless `review-readiness` event is ignored there, same as existing `review-intake`/`review-result`. Holds.
- `review_readiness` is new; both callers are in the diff (:233, :271). `history` at :233 is pre-filtered by `pr`, at :271 it is the full log; the inner `e.get("pr") == n` (:298) makes both correct.

### Standards

No AGENTS.md/CONTRIBUTING.md in repo; README is the documented convention source. No abstractions added beyond one function; style matches surrounding code.

### Required fixes

None.

### Optional suggestions

1. **README now contradicts the code.** README.md:349 states "This lane does not change contributor branches, monitor CI, or merge external PRs." After dispatch.py:276-279 the lane polls `gh pr checks` every pass. Update that sentence and document the `review-readiness` event alongside `review-intake` at README.md:337-339 (agent/5 and agent/6 updated this section; agent/7 did not, so no hard rule exists).
2. **Unbounded per-pass polling and journal growth.** dispatch.py:233 runs `review_readiness` for every tracked PR on every pass, unconditionally appending an event and issuing two `gh` calls, until the PR closes. Previously an approved, un-labelled PR cost zero calls (`continue` at :234). With a 10-min timer that is ~144 rows/PR/day in `.factory/events.jsonl`, a file every consumer and :221/:231 re-read in full per PR. One-line mitigation: skip `record` (or the whole call) when the last `review-readiness` row for `n` already has the same `head`/`state`. Not blocking: issue asks to "track" check state and no caller is broken.
3. `rows = json.loads(checks.stdout)` raising at :285 skips the fresh-head read entirely, so an unparsable checks payload also leaves `head` unrefreshed and `review_pending`/`changes_requested` is recorded against the stale head. Still fail-closed (never `ready`); splitting the two parses would make the recorded `head` accurate. Cosmetic.

VERDICT: APPROVE

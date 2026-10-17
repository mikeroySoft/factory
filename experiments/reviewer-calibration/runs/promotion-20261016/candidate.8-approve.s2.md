## Standards

Standards sources found: no `AGENTS.md`/`CONTRIBUTING.md` in repo; documented contract lives in `README.md` (review-lane section, 335-349) and `docs/`. Code style of the diff matches the surrounding `review_intake_pass`/`record()` patterns (`run([...], check=False)`, `lifecycle.read_events`, head-bound events).

### Required

1. **README lane contract now false.** `README.md:349` documents: "This lane does not change contributor branches, **monitor CI**, or merge external PRs." The diff makes the lane query `gh pr checks` and `gh pr view` for every tracked PR each pass (`factory/dispatch.py:276-282`, called at `factory/dispatch.py:233` and `:271`) and emits a new `review-readiness` event (`factory/dispatch.py:311`). Issue #8 supersedes the "no CI monitoring" statement, so the fix is the doc, not the code: amend `README.md:349` and add `review-readiness` (fields `pr`, `head`, `state`, `review_head`, `checks`; states `review_pending|changes_requested|ci_pending|ci_failed|ready`) next to the `review-intake` event description at `README.md:337-338`. Impact if left: an operator reading the documented contract is told the lane makes no CI calls and emits no readiness evidence while it does both every 5 min (`factory/onboard.py:301`).

### Optional (judgement calls)

- **Journal growth / bounded-window crowding.** `factory/dispatch.py:233` records a `review-readiness` row for every tracked PR on every pass, unconditionally, and `:220` keeps every ever-approved open PR tracked for life. With the 5-min timer that is ~288 rows/PR/day, each with the `checks` list. Consumers read bounded tails (`factory/runtime_events.py:15-16` 1 MiB / 512 events; `factory/briefing.py:29`), so steady-state readiness rows push out execution evidence. Smallest fix: skip `record` when the last `review-readiness` row for `pr` already has the same `(head, state)`. Add when the journal is observed to fill.
- **Two rows per newly admitted revision.** `:233` records `review_pending` before the review, then `:271` records again after it. Harmless; a `continue`-ordering tweak would avoid the first row for not-yet-reviewed heads.
- **Head confirmation skipped when check JSON is bad.** `factory/dispatch.py:285-286`: `json.loads(checks.stdout)` raising skips the `fresh` parse, so a push plus a failed checks call records the stale list head (`ci_pending` for head-1 rather than `review_pending` for head-2). Never yields `ready` (`:307` requires `confirmed`), so fail-closed holds; two separate `try` blocks would make the recorded head accurate.
- **Possible Duplicated Code** (baseline smell): `review_readiness` re-implements `pr_checks` (`factory/dispatch.py:978-992`) with `--required` and a returncode. Reuse would need a signature change touching merge-stage callers; leaving it is reasonable.
- **Existing test stubs now hit new commands.** `tests/test_factory.py:1548-1550` forwards non-`gh api` commands to `real_run`, so `gh pr checks`/`gh pr view` now execute the real `gh` binary in that test (it fails closed via `check=False`, gate PASS). `tests/test_factory.py:1447-1452` answers them with `"diff"`. Both tolerate the change; routing them to an explicit stub would remove the environment dependency.
- No `CHANGELOG.md` Unreleased entry; prior lane tickets #5-#7 didn't add one either, so not a rule.

## Spec

Issue #8 exit gate vs diff:

| Criterion | Evidence |
|---|---|
| Missing / unparsable checks not ready | `factory/dispatch.py:292-296` (`valid` requires non-empty list of `{name:str, bucket∈…}`), `:307`; tests `tests/test_factory.py:1396,1397,1398,1401,1402,1403` |
| Pending / failed / cancelled not ready | `:305-306` (`fail`/`cancel` → `ci_failed`), `:307-310` (`ready` needs all `pass` and rc 0); tests `:1399,1400,1404-1406` |
| Approval + passing required checks for the same head → ready | `:297-299` picks the latest `review-result` bound to the fresh head; `:307` requires `confirmed`; test `:1409` |
| New head clears readiness | `:287-289` fresh head replaces `head`; review lookup for head-2 finds none → `review_pending`; tests `:1413-1419` |
| Distinct states ci pending / ci failed / changes requested / ready | `:300-310`; test `:1420-1422` for `changes_requested` |
| Events via existing `record()` | `:311-312` |
| No `gh pr merge` for external PRs | no merge call in the diff; asserted `tests/test_factory.py:1426` |
| Dry run does not write | `:232`, `:268`; test `:1423-1425` |
| `python -m unittest` passes | gate `test: PASS` |

Scope: the eligibility widening at `factory/dispatch.py:218-223` (track PRs with a recorded `APPROVE`, not just `REQUEST_CHANGES`) is needed for the criterion "approval + passing checks → ready": GitHub consumes the review request on approval and CI usually finishes afterwards, so without it an approved PR would drop out of the lane before CI could be observed. In scope. No out-of-scope behaviour (branch pushes, merges, dashboard) added.

## Changed behaviour → consumers outside the diff

- Eligibility predicate `factory/dispatch.py:218-223`: sole caller `review_intake_pass` ← `factory/dispatch.py:1577` (main pass). Still holds: dry-run path unchanged; approved PRs now stay tracked but hit `continue` at `:234-236`, so no re-review/escalation behaviour changes. Existing lane tests `tests/test_factory.py:1457-1471,1509-1515,1559,1612,1664` drive the same path; gate PASS confirms they tolerate the extra `gh pr checks`/`gh pr view` calls (stubs at `:1447-1452`, `:1548-1555`).
- `review_readiness` is new; no external callers. `review-readiness` event name is unknown to every journal consumer (`grep` across `factory/`: only `dispatch.py`); `stats.audit_by_ticket`, `dashboard.spend_by_ticket` key on `ticket`/`attempt` and ignore it; `briefing.py:261-263` dumps all rows verbatim.
- No signatures, flags, profiles, or config defaults changed.

**Summary:** Standards: 1 required (stale README contract at `README.md:349`), 5 optional. Spec: all exit-gate criteria met, 0 findings.

VERDICT: REVISE

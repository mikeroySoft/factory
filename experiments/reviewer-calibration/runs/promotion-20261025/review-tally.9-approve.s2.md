**Scope check.** Issue #9 touches: `factory/dashboard.py`, `factory/dashboard.html`, `tests/test_factory.py`, `README.md` — the diff changes exactly those four files. No plan recorded, so drift is measured against the issue's Touches list: zero unnamed files. Gate covers `python -m unittest`; the exit gate's manual check ("`factory dashboard` displays the queue without changing labels…") is not in the gate report, but `snapshot()` only calls the mocked `github()` reader and the test asserts `events.jsonl` is byte-identical afterward (`tests/test_factory.py:2823`), which is the reachable proof of non-mutation.

**Acceptance trace.**
- Six states: `review_queue()` (`factory/dashboard.py:843-851`) derives `review_pending / changes_requested / ci_pending / ci_failed / ready / escalated`; test asserts all six (`tests/test_factory.py:2826-2828`).
- SHA-specific: `current`/`readiness` matched on `head == headRefOid` (`dashboard.py:834-837`), `review_head` is the last review's head regardless (`:855`); test PR 1 verifies old-head approval → `review_pending`, `ci_state: unknown` (`:2834-2836`). Matches dispatch's `review_readiness` semantics (`dispatch.py:297-310`) and the README claim "an old approval never makes a new head ready".
- Opt-in parity with intake: label, review request to viewer, or prior admitted verdict (`dashboard.py:828-831`) mirrors `dispatch.py:211-223`; test covers consumed request (`:2812`) and non-viewer request (PR 7).
- Links + inbox without mutation: rows link `pr.url` (`dashboard.html:1138`); inbox items carry only GitHub guidance text (`:1565-1566`); `informational` list (`:1559`) never sees `external-review` items since that kind is in `WAITING`, so `i.t.number` there is safe.
- Event field names (`head`, `verdict`, `checks[].bucket`, `state`, `at`, `reason`) all match `record()` producers (`dispatch.py:260-262, 311-312, 363, 408`).

No standards docs (AGENTS.md/CONTRIBUTING.md) exist in the snapshot; only `pyproject.toml` ruff config and CHANGELOG practice are observable conventions.

```json
{"verdict":"APPROVE","findings":[
{"severity":"nit","pass":"bug","path":"factory/dashboard.py","line":857,"summary":"`current.get(\"reason\")` is dead: `review-result` rows are recorded without a `reason` field (dispatch.py:363), so the `pr.reason` branch of the review_pending inbox condition (dashboard.html:1162) is unreachable; only `escalation.reason` ever populates it."},
{"severity":"nit","pass":"spec","path":"factory/dashboard.py","line":875,"summary":"Queue is sourced from the existing GraphQL `pullRequests(first:100, CREATED_AT desc)` over all states, while intake admits from `gh pr list --state open --limit 1000` (dispatch.py:204). In a repo with >100 total PRs, an admitted opted-in PR older than the newest 100 is silently absent from the queue (issue: 'Show each opted-in PR')."},
{"severity":"nit","pass":"spec","path":"factory/dashboard.py","line":825,"summary":"104 chars exceeds `[tool.ruff] line-length = 100` (pyproject.toml:37). Not enforced in CI (ci.yml runs unittest only) and 27 pre-existing violations exist, so informational."},
{"severity":"nit","pass":"spec","path":"README.md","line":353,"summary":"CHANGELOG.md `Unreleased` not updated; prior dashboard features (#79, #67) are recorded there with issue numbers. Not a documented rule, so informational."}
]}
```
VERDICT: APPROVE

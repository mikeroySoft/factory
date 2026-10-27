**Scope check (issue #20 + triage brief):** optional `when` on `[workers.<label>]` tables (`config.py:289-298`), manager prompt lists labels with `when` (`manage.py:199-202`), ROUTE/FIX restricted to listed labels (`manage.py:60-66`, `parse` receives the filtered dict at `manage.py:213`), `ci-fix`/`conflict` profiles in the template (`factory.toml:41-47`), array form still loads (`tests/test_factory.py:150-158`), dashboard exposes `worker_when` (`dashboard.py:847`). Exit-gate scenarios are covered by `test_route_uses_only_listed_worker_labels_and_when_rules` and `test_fix_runs_selected_worker_on_red_ci_and_requires_gate_and_review`; gate `test: PASS`.

**FIX path correctness:** mirrors the existing review-bounce flow (`dispatch.py:1056-1070`): worker_round → gate → push → review → `approve_pr` or `escalate`. Preconditions (kept worktree, open PR on `agent/<n>`, branch check) fail into the existing `mechanism_failure` handler. No requeue on FIX, matching README. Host-layer guidance ("keep a default worker in that layer") is accurate: `merge` is recursive, but a repo whose `[workers]` is fully commented contributes no `default`, so `config.py:286` would raise.

**Plan drift:** no plan recorded. Files beyond the issue's "Touches": `README.md`, `tests/test_factory.py` — both docs/tests for the feature, not defects.

**Standards:** no AGENTS.md/CONTRIBUTING.md in repo; README is the documented reference. Template convention (all defaults commented) is followed.

```json
{"verdict":"APPROVE","findings":[
{"severity":"nit","pass":"spec","path":"factory/templates/factory.toml","line":41,"summary":"Issue says \"Ship two default profiles\"; they ship as commented-out examples a human must copy into host config. Consistent with the template's comment-everything convention and the \"human-applied\" clause, but a repo running `factory init` gets no ci-fix/conflict worker until someone edits config — confirm this reading is intended."},
{"severity":"nit","pass":"spec","path":"README.md","line":176,"summary":"Config reference sample still shows only the array form; the table/`when` form is described in prose at README.md:93-95 but not in the sample (same for docs/index.html:316)."},
{"severity":"nit","pass":"bug","path":"factory/manage.py","line":122,"summary":"`worker_round` returns ok=False with report \"budget exceeded before gate\" when the deadline passes; FIX escalates that as \"gate failed after manager FIX\", mislabelling a budget timeout as a gate failure in the packet/comment."},
{"severity":"nit","pass":"spec","path":"factory/manage.py","line":223,"summary":"Comment \"A partial SPLIT or REWRITE must not be replayed automatically\" is now also the reason FIX failures (push/review after worker ran) land in mechanism_failure; comment is stale."}
]}
```
VERDICT: APPROVE

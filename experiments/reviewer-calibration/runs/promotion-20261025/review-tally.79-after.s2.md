Diff reconstructed from `git diff 8fba9c5..e20ebcd` (`./review.diff` and `./snapshot` absent; working tree is the gated head). Issue #79 brief, triage comment, and escalation comment read.

## Standards
No `CONTRIBUTING.md`/convention doc; `pyproject.toml` only sets `line-length = 100` (not enforced: `briefing.py` already has 60 over-length lines). Rendering goes through existing `h()`/`pre()` text-node helpers and `safeSourceURL` (`factory/dashboard.html:464,1228`), satisfying the "safe DOM/text helpers" rule. Transport reuse: `feedback.collect` takes the existing `dashboard.github` (`factory/dashboard.py:888`); no second client, cache, or event append. One cross-module private import (`lifecycle._lifecycle`, `factory/feedback.py:406`).

## Spec
Schema-1 envelope, `REVISION_FIELDS`, `observation_id` inputs, bounds (100/2/20000/32/30s), initial+final head reads, head-race → partial+unknown, outdated-thread override, provenance-gated `factory_review` (checks `returncode/parsed/head==actual_head/enter/exit outcome`, matching real rows at `factory/dispatch.py:489-492`), `Closes #n` → `closingIssuesReferences` ownership (`factory/dispatch.py:1319`), runtime-json path untouched (`factory/dashboard.py:1228-1235`), briefing appends feedback after existing candidates with omission notice, legacy `pr.checks/comments/verdicts/review_decision/gate_text` preserved (tested `tests/test_factory.py:1319-1323`). Exit-gate items 1–4, 6 have tests. No required criterion missed.

## Plan drift
Plan not recorded. Files vs issue "Touches": all in list; `tests/test_feedback.py` is the conditionally-allowed extra. Gate report lacks proof for exit-gate item 5 (disposable-repo CLI + real-browser Review/Inbox smoke, `sources_for` bundle) and item 7 (handoff packet); the escalation comment carries a snapshot example but browser observations are not in evidence here. Not a defect by itself.

```json
{"verdict":"APPROVE","findings":[
{"severity":"nit","pass":"spec","path":"factory/dashboard.py","line":895,"summary":"collect_details=(state==OPEN) skips detail reads for closed/merged PRs; issue never scoped an open-only bound and 'unavailable' there means chose-not-to-read, not cannot-read. Documented in README/CHANGELOG and non-actionable, so judgment call; confirm it was an approved scope change."},
{"severity":"nit","pass":"bug","path":"factory/dashboard.py","line":878,"summary":"git status --porcelain runs on the source checkout every 30s refresh without --no-optional-locks (repo's own read path uses it, factory/runtime_local.py:63); can take index.lock in a dev checkout and collide with operator git commands."},
{"severity":"nit","pass":"bug","path":"factory/feedback.py","line":251,"summary":"On source_identity_conflict the prior item is removed but counts[source] is not decremented, so a conflicted identity still consumes one of the 100 slots."},
{"severity":"nit","pass":"bug","path":"factory/feedback.py","line":406,"summary":"Imports private lifecycle._lifecycle across modules; expose a public validator or reuse an existing public reader."},
{"severity":"nit","pass":"spec","path":"factory/briefing.py","line":356,"summary":"Omitted feedback items are counted in both the 'PR feedback evidence item(s) omitted' and 'additional evidence source(s) omitted' notices, double-reporting the same omission."}
]}
```
VERDICT: APPROVE

**Standards.** No AGENTS.md/CONTRIBUTING.md in the snapshot; only enforced gate is `python -m unittest discover -s tests` (`.github/workflows/ci.yml:12`). `pyproject.toml:36-37` configures `ruff line-length = 100` but ruff isn't run; `feedback.py` has 69 lines over it (existing `dashboard.py`/`briefing.py` already exceed too). `feedback.py` uses single quotes throughout vs. double quotes in every other module. Style nits only.

**Spec (#79).** Traced end to end:
- `snapshot()` → `feedback.collect(github, …)` for selected issues with an `agent/<n>` PR; `collect_details` only for `OPEN` (`dashboard.py:596-608`). Runtime-JSON branch returns before `snapshot()` (`dashboard.py:1228-1254`, unchanged) — network-free contract holds.
- Transport: `github()` gains fixed `endpoint`/`query`/`variables`/`timeout` (`dashboard.py:510-537`); `-F` only for ints, `None` vars skipped — valid for `$cursor:String`. GraphQL field sets (`reviews{… commit{oid} updatedAt}`, `reviewThreads{isResolved isOutdated comments{line originalLine diffSide originalCommit}}`, `closingIssuesReferences`) and REST check-runs/statuses match provider schemas.
- Factory review provenance matches what `dispatch.review` actually emits (`dispatch.py:489-492`: `result` row with `verdict/parsed/head/actual_head/returncode`, exit outcome `approved|product_feedback`), and Factory PR bodies carry `Closes #n` (`dispatch.py:1319`), so `closingIssuesReferences`-based ownership works in production, not just fixtures. Factory posts a PR *comment*, not a provider review → no double count.
- Schema-1 envelope, revision field set (`REVISION_FIELDS`), observation digest inputs, bounds (100/2/20000/32/30), head-race handling, sort order, sanitized fixed error strings, `not_collected` for closed/merged PRs — all present and asserted in `tests/test_feedback.py`.
- Briefing appends feedback after human decisions and reports omissions (`briefing.py:156-247`); HTML renders bodies via `pre` → text nodes, `href` via `safeSourceURL`, `h()` drops `open:false`.
- Legacy `pr.checks/comments/verdicts/review_decision/gate_text` preserved (`tests/test_factory.py:1319-1323`).

**Plan drift.** Plan not recorded. Files changed are exactly the issue's "Touches" list plus the explicitly permitted `tests/test_feedback.py`. Gate report covers exit gate 6 only; exit gates 5 (disposable-repo CLI + real-browser Review/Inbox check) and 7 (handoff with sanitized example) and the DOM-level `<img onerror>` literal-render check from gate 4 are not evidenced in the gate report or diff (no JS harness exists; fixture body is present at `tests/test_feedback.py:1420`). These are proof gaps for the handoff, not defects in the diff.

```json
{"verdict":"APPROVE","findings":[
{"severity":"nit","pass":"spec","path":"factory/dashboard.py","line":600,"summary":"Feedback collection runs inside snapshot() under _cache_lock: up to 8 sequential gh calls per open PR with a 30 s per-PR cap. With several open agent PRs this can exceed SNAPSHOT_TTL=15 s and the 30 s client poll, blocking all /api/snapshot readers; cadence risk, design as specified by the issue."},
{"severity":"nit","pass":"spec","path":"factory/feedback.py","line":1020,"summary":"ownership_unverified is recorded as a gap on the pr source, so a fully successful PR read is reported as coverage.pr.status=partial (rendered PENDING in the drawer) whenever ownership isn't factory_issue; ownership is already expressed in owner.relation."},
{"severity":"nit","pass":"spec","path":"factory/dashboard.py","line":583,"summary":"A missing .factory/events.jsonl (fresh repo) sets provenance_complete=False, so every open PR gets reviews coverage partial/truncated with provenance_partial even though no Factory review can exist yet."},
{"severity":"nit","pass":"bug","path":"factory/briefing.py","line":249,"summary":"feedback_omitted items are also counted in omitted, so the two notices double-count the same evictions ('1 PR feedback … omitted' and 'N additional evidence source(s) omitted')."},
{"severity":"nit","pass":"bug","path":"factory/feedback.py","line":755,"summary":"A read that returns successfully after the deadline is discarded as 'timeout' rather than retained; conservative but drops observed facts."},
{"severity":"nit","pass":"bug","path":"factory/dashboard.py","line":530,"summary":"Fallback error text 'gh api graphql failed' is now also used for REST endpoint reads."},
{"severity":"nit","pass":"spec","path":"factory/feedback.py","line":829,"summary":"69 lines exceed the configured [tool.ruff] line-length = 100 (pyproject.toml:37); not CI-enforced and pre-existing modules also exceed."},
{"severity":"nit","pass":"spec","path":"factory/feedback.py","line":640,"summary":"Single-quote string style diverges from the double-quote convention used in every other factory/*.py module."},
{"severity":"nit","pass":"spec","path":"tests/test_feedback.py","line":1420,"summary":"Exit gate 4's '<img onerror=...> renders literally' is only present as collector fixture data; no DOM-level assertion (no JS harness) — needs the gate-5 browser observation in the handoff."}
]}
```
VERDICT: APPROVE

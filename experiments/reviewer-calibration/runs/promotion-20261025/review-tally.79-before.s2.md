Reviewed the full diff (`review.diff`, 1497 lines), issue #79 body + both comments, and the snapshot sources the diff depends on (`dashboard.py` `snapshot`/`cached_snapshot`/`sh`/`selected_issue`, `lifecycle._lifecycle`, `dispatch.review` result events, `briefing.add`/`bounded_file`, `dashboard.html` `h`/`safeSourceURL`, `test_runtime.py` audit).

## Standards
- No CONTRIBUTING.md/AGENTS.md in the repo; CI runs `python -m unittest discover -s tests` only. README and CHANGELOG updated as the issue requires. No convention breach found.

## Spec trace
- Schema-1 envelope, items fields, enums, explicit nulls, `source_revision` over exactly `REVISION_FIELDS`, `observation_id` over native IDs/final head/sorted pairs/coverage status-truncated-reason: matches (`feedback.py:24-26`, `:230`, `:405-407`).
- Factory review provenance: requires lifecycle `result` with `returncode==0`, `parsed`, `head==actual_head`, and bracketing `enter`/`exit` with `approved`/`product_feedback` — matches what `dispatch.review` emits (`dispatch.py:477,489-492`). `VERDICT` prose rejected.
- Ownership requires same-repo closing-issue link + `claimed` event (`dispatch.record("claimed", ticket=n)` shape matches).
- Head race / final-head failure / one-source failure / bounds: handled and tested.
- Runtime-JSON path doesn't call `snapshot()`; `test_runtime.py` already audits commands.
- Briefing appends feedback after existing candidates and reports omissions; unsupported/unknown schemas not interpreted.
- Dashboard renders bodies via `pre()`/text nodes; URLs via `safeSourceURL`.

## Defect
`snapshot()` now runs `feedback.collect` for every selected ticket with an agent PR — open, closed, and merged alike (`dashboard.py:883-892`; `raw_prs` is filled from `pullRequests(first:100)` with no state filter). Each collect performs ≥6 serial `gh` subprocess reads (2 head GraphQL, ≥1 reviews REST, ≥1 threads GraphQL, 2 checks REST: `feedback.py:131-132,246,267,311-313,339`) with a per-PR 30 s budget. `snapshot()` is executed synchronously under `_cache_lock` with `SNAPSHOT_TTL = 15` (`dashboard.py:84,962-967`). With N agent PRs this is 6N+ network subprocesses per snapshot (tens of PRs → minutes per `/api/snapshot`, worst case 30 s × N under provider trouble), and `/api/snapshot` blocks all requesters for its duration. Issue #79 scope: "Preserve full-snapshot cadence and existing ticket/PR fields." The 15 s cadence is not preserved for any repository with more than a handful of agent PRs.

## Plan drift
Plan not recorded. All changed files (`CHANGELOG.md`, `README.md`, `factory/briefing.py`, `factory/dashboard.html`, `factory/dashboard.py`, `factory/feedback.py`, `tests/test_briefing.py`, `tests/test_factory.py`, `tests/test_feedback.py`) are named in the issue's Touches. Gate report covers only conflict-markers/test/leak-scan; exit-gate 5 smoke (real `--json` run, live browser Review drawer/Inbox check, `sources_for` bundle) and exit-gate 7 handoff record are not in the gate report. Not blocking by itself.

```json
{"verdict":"REVISE","findings":[{"severity":"important","pass":"spec","path":"factory/dashboard.py","line":883,"summary":"feedback.collect runs synchronously for every selected agent PR (open/closed/merged, up to 100) inside snapshot(), each doing >=6 serial `gh` subprocess reads with a 30 s budget (feedback.py:131,246,267,311,339); snapshot() executes under _cache_lock with SNAPSHOT_TTL=15 (dashboard.py:84,962-967). Issue #79 scope rule 'Preserve full-snapshot cadence' is violated: /api/snapshot and --json take minutes with tens of PRs and block all requesters. Bound collection (e.g. open/non-merged PRs or the selected ticket) or move it off the request path."},{"severity":"nit","pass":"bug","path":"factory/dashboard.py","line":216,"summary":"github() now raises RuntimeError('GitHub read failed') for the main GraphQL snapshot query too, dropping gh's stderr that previously surfaced in snapshot errors ('github: ...'); feedback.py already replaces messages with sanitized codes, so sanitizing here only degrades operator diagnostics for the pre-existing full-snapshot path."},{"severity":"nit","pass":"spec","path":"factory/dashboard.py","line":857,"summary":"Missing .factory/events.jsonl (bounded_file -> None) marks provenance_complete=False, so every PR gets a reviews 'provenance_partial' truncated gap on a fresh repository with no journal; an absent journal is a complete empty provenance read, not a cut one."}]}
```

VERDICT: REVISE

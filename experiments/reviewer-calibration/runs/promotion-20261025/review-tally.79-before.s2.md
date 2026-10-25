## Standards

No AGENTS.md/CONTRIBUTING.md in repo; `docs/` holds plans, not coding rules. Conventions observed from the code (stdlib-only, `h()`/`pre()` text-node rendering, bounded reads via `briefing.bounded_file`, lifecycle validation via `lifecycle._lifecycle`) are followed. `feedback.py` is the one new module the issue asked for; no event bus/second client/cache introduced.

Smell notes (judgement calls, non-blocking):
- `tests/test_factory.py:2401` imports from `tests.test_feedback` — first cross-test import in the suite, no `tests/__init__.py`; works only because `python -m` puts cwd on `sys.path`.
- `factory/dashboard.py:216` replaces `proc.stderr` with a fixed `"GitHub read failed"`. Feedback already sanitizes in `feedback.request` (`feedback.py` `source_unavailable`), so this only degrades the pre-existing `snapshot()["errors"]` diagnostic (e.g. `gh auth` hints) for operators.

## Spec

Schema-1 envelope, `source_revision` field set, `observation_id` inputs, bounds (`ITEM_LIMIT/PAGE_LIMIT/BODY_LIMIT/ERROR_LIMIT/DETAIL_TIMEOUT`), head race → `unknown`, outdated-thread rule, provenance-gated `factory_review` via accepted lifecycle `result` events (`returncode==0`, `parsed`, `head==actual_head`, enter/exit bracket), ownership via `closingIssuesReferences` + retained `claimed` event (matches `dispatch.record("claimed", ticket=n)`), runtime-JSON path untouched (`dashboard.py:1224-1250` never calls `github()`/`snapshot()`), HTML rendering only through `h()`/`pre()` text nodes — all present. README/CHANGELOG document constants and unknown/partial semantics.

Deviations:
1. **Snapshot cadence (important).** `dashboard.py:883-892` runs `feedback.collect` for every selected issue with a PR — `selected_issue` (`dashboard.py:722`) includes any `number in prs`, i.e. every `agent/N` PR in the 100-PR GraphQL window, merged/closed included. Each collection spawns 6–8 `gh` subprocesses (two `HEAD_QUERY`, 1–2 reviews, 1–2 threads, check-runs, statuses) with a 30 s budget (`feedback.py:22`). For an active repo (this one has ~100 agent PRs) that is several hundred subprocess calls per snapshot, serialized under `_cache_lock` in `cached_snapshot` (`dashboard.py:962-965`) against a 15 s `SNAPSHOT_TTL` (`dashboard.py:84`). Issue text: "Preserve full-snapshot cadence and existing ticket/PR fields" (Existing read-surface integration, bullet 1). No test or bound covers aggregate cost; `/api/snapshot` and `--json` can stall for minutes. Needs a bound (e.g. restrict collection to open PRs or a shared per-snapshot budget) — spec leaves the mechanism open; it does not require collecting closed/merged PRs.
2. `feedback.py:258,327`: REST reviews and check-runs carry no `updated_at`, so `source_updated_at` is always null for `review`/`check_run`, and every truncated body gets `change_detection_incomplete` even though GraphQL `PullRequestReview.updatedAt` exists. Spec: "actual provider UTC update time when supported". README documents the check-run null, not the review null. Nit.
3. Exit gate 4 asks for a regression check that `--runtime-json` performs no feedback/GitHub read; no such test is added (path verified structurally separate, so not a defect).

## Plan drift

Plan not recorded. All nine changed files are named in the issue's "Touches" list; no unnamed files. Gate report covers conflict-markers/test/leak-scan only; exit gate 5 (disposable repo `--json` run, live browser Review drawer/Inbox check, `sources_for` bundle inspection, mutation-absence record) and gate 7 handoff are not evidenced in the gate report. The issue comment's handoff excerpt shows a snapshot JSON fragment, implying the CLI run happened; browser evidence absent. Not a defect by itself.

```json
{"verdict":"REVISE","findings":[
{"severity":"important","pass":"bug","path":"factory/dashboard.py","line":885,"summary":"feedback.collect runs for every selected ticket with an agent/N PR (merged/closed included, up to the 100-PR window), each spawning 6-8 gh subprocesses with a 30 s budget, serialized under _cache_lock against SNAPSHOT_TTL=15; violates 'Preserve full-snapshot cadence' and can stall /api/snapshot and --json for minutes. Bound the aggregate (e.g. open PRs only or a per-snapshot budget)."},
{"severity":"nit","pass":"spec","path":"factory/feedback.py","line":258,"summary":"REST review objects have no updated_at, so review source_updated_at is always null and every truncated review body is flagged change_detection_incomplete; spec asks for provider update time when supported (GraphQL PullRequestReview.updatedAt). README documents the check-run null but not this one."},
{"severity":"nit","pass":"bug","path":"factory/dashboard.py","line":216,"summary":"github() now discards gh stderr for the main snapshot query; feedback already sanitizes in feedback.request, so this only degrades the pre-existing snapshot errors[] diagnostic shown to operators."},
{"severity":"nit","pass":"spec","path":"tests/test_factory.py","line":2401,"summary":"No test asserts --runtime-json performs no feedback/GitHub read (exit gate 4); also first cross-test import (tests.test_feedback) without tests/__init__.py, relying on cwd being on sys.path."}
]}
```
VERDICT: REVISE

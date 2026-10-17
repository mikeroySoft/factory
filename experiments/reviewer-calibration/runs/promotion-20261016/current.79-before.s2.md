## Standards

No `AGENTS.md`/`CONTRIBUTING.md` in the repo; `docs/` holds plans, not coding rules. Only the smell baseline applies (judgement calls, all optional).

**Optional**
- `factory/feedback.py:70` — possible Long Function: `collect` is ~330 lines with four nested closures; `item()` (`feedback.py` `def item(...)`) takes 15 keyword params (Data Clump: review/thread/check IDs + disposition travel together). Readable as-is; no refactor demanded.
- `tests/test_factory.py:2401` — `FeedbackSnapshotTest` imports `Provider, REPO, PR, ISSUE, H, AT, factory_events` from `tests.test_feedback`; cross-test-module coupling. Fine while both files exist.
- `tests/test_briefing.py:58,78,88` — fixture values (`revision: "8fba9c5"`, `review_state: "changes_requested"`, evidence `"branch agent/7"`) don't match what the producer emits (40-hex or null, uppercase states, branch text never evidence). Harmless since `sources_for` is schema-agnostic, but misleading as documentation.
- `factory/dashboard.py:216` — `RuntimeError("GitHub read failed")` replaces `proc.stderr` for the legacy full-snapshot GraphQL call too, so `snapshot()["errors"]` loses auth/rate-limit diagnostics operators had. Sanitization is only required on the feedback path, and the collector already replaces messages at `feedback.py:123-126`. Consider keeping stderr for the default query.

## Spec

Schema-1 shape, canonicalization, `source_revision`/`observation_id` field sets, fixed bounds, head-race handling, coverage semantics, ownership gating, Factory-review provenance (lifecycle `result` row with `returncode==0`, `parsed`, `head==actual_head`, bracketing enter/exit), briefing append-after-constraints with omission notices, HTML rendering via `h()`/`pre()`/`safeSourceURL`, and runtime-json untouched (`dashboard.py:1224-1250` never calls `snapshot()`; `tests/test_runtime.py` audits commands) all match the issue text. Exit gates 1–4 are covered by `tests/test_feedback.py`, `tests/test_briefing.py:97-162`, `tests/test_factory.py:2397-2448`.

**Required**

1. `factory/dashboard.py:879-893` — feedback is collected for every selected issue with a PR. `selected_issue` (`dashboard.py:722`) selects `number in prs`, and `prs` holds the latest 100 PRs including merged/closed. Each `feedback.collect` issues up to 8 serial `gh` subprocesses (2 head + 2 reviews + 2 threads + 2 checks) with a 30 s budget; `snapshot()` runs under `_cache_lock` (`dashboard.py:962-967`) every 30 s of UI auto-refresh (`dashboard.html:1650`). Trigger: any repo with a few dozen historical PRs in the window. Impact: ~7N subprocess reads per snapshot (N=50 → ~350 calls/30 s → exceeds GitHub's 5000/h limit within minutes, which then fails the legacy GraphQL read too), and snapshot latency exceeds the refresh cadence while `/api/snapshot` blocks. Violates issue §Existing read-surface integration: "Preserve full-snapshot cadence and existing ticket/PR fields." Minimal fix: collect only for open PRs (`prs[n]["state"] == "OPEN"`); closed/merged PRs then carry no `feedback` key — if that reads as "unsupported engine" in the UI, attach an all-`unavailable` schema-1 object without network reads instead.

2. `factory/dashboard.py:850-873` — provenance is re-read from `events.jsonl` via `briefing.bounded_file` (unlocked, 2 MB tail, hand-rolled torn-line/JSON handling) even though the same function already holds the accepted committed, flock'd view in `rows` (`dashboard.py:837`, `lifecycle.journal_snapshot` → `_rows`). Issue §Scope: "Reuse the existing full-snapshot GitHub transport and accepted bounded lifecycle/event readers for provenance." Concrete divergence: `lifecycle._write` deliberately writes a `\x00\n` tombstone after an interrupted append (`lifecycle.py:51-53`) and `_rows` ignores it ("ignoring legacy malformed/interrupted lines", `lifecycle.py:69`); this block instead hits `except ValueError: provenance_complete = False` (`dashboard.py:868-869`), so one tombstone or legacy non-dict line anywhere in the tail marks `coverage.reviews` `partial`/`truncated=True` with `provenance_partial` for every PR, permanently, and perturbs `observation_id`. Fix: `events=rows`, `provenance_complete=True`, delete lines 850-873 (and update `tests/test_factory.py:2432-2438`, which patches `bounded_file`).

3. `factory/dashboard.py:874-878` — `git rev-parse HEAD` runs in the installed package dir and `git status --porcelain -- .` is the only cleanliness check. Trigger: Factory installed into a gitignored `.venv/` inside the target project checkout (the normal layout). `git status --porcelain` omits ignored paths, so the check passes and `producer.revision` becomes the *target project's* HEAD. Issue §Normative schema 1: "`revision` is a verifiable source/build revision when available, otherwise null." A misattributed SHA is handed to the #15 integration owner as the accepted producer revision (exit gate 7). Fix: gate on the package being tracked, e.g. `sh(["git", "ls-files", "--error-unmatch", "feedback.py"], cwd=source_root)` non-empty, else `None`.

**Optional**
- `factory/feedback.py` reviews loop (`for row in values[:ITEM_LIMIT]` / `item()` cap check): the `counts[source] >= ITEM_LIMIT` check precedes identity dedup, so a repeated page (as in the test stub) reports `item_limit` for already-retained IDs. Real `per_page=50` paging never triggers it; no change needed.
- `README.md:699-772` is a long prose restatement of the issue; a short schema-1 example (already in the handoff comment) would be more useful than the paragraph on #15 authority. Not a rule.

Summary — Standards: 4 optional, worst: stderr dropped from legacy snapshot errors. Spec: 3 required, worst: per-PR serial `gh` fan-out on every 30 s snapshot (cadence/rate-limit).

VERDICT: REVISE

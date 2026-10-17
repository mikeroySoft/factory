## Standards

Standards sources: no `CONTRIBUTING.md` / `docs/` coding standard in repo; applied `~/.omp/agent/AGENTS.md` (§2 Simplicity, §3 Surgical) plus the smell baseline.

**Optional (judgement calls)**

- **Duplicated Code / reuse** — `factory/dashboard.py:850-873` re-reads `.factory/events.jsonl` through `briefing.bounded_file(..., tail=True)` and hand-parses JSON lines, while the same function already holds the committed, lock-coherent journal as `rows` from `lifecycle.journal_snapshot` (`dashboard.py:837`). Side effect: once the journal exceeds `EVENT_READ_CAP` (2 MB) every PR gets `reviews` coverage permanently `partial` via `provenance_partial` (`factory/feedback.py:370-371`) even though the full rows are in memory. `events=rows, provenance_complete=True` is the one-line alternative.
- **Orphan field** — `headRefOid` added to the main `GRAPHQL` at `dashboard.py:150` is unused anywhere in `dashboard.py` (AGENTS.md §3: remove what your change made unused).
- **Diagnostic loss** — `dashboard.py:216` replaces `proc.stderr` with `"GitHub read failed"`, so the pre-existing snapshot `errors: ["github: …"]` entry no longer carries `gh`'s auth/permission text. Sanitization is already done in `feedback.request` (`feedback.py:124-127`); the legacy path didn't need it.
- **Private import** — `feedback.py:16,383` depends on `lifecycle._lifecycle`.
- **Long function** — `collect()` (`feedback.py:72-408`) is ~330 lines with five closures sharing mutable state. Not in the baseline list; noting only.
- `tests/test_factory.py:2401` imports fixtures from `tests.test_feedback` (cross-test coupling).

## Spec

Schema-1 envelope, item fields, `REVISION_FIELDS`, `observation_id` inputs, bounds (`feedback.py:18-22`), head-race/final-head-unavailable handling (`feedback.py:339-351`), ownership verification via closing-issue link + `claimed` event (`feedback.py:353-368`, matches `dispatch.py:1297`), Factory-review acceptance mirrors `dispatch.review` (`feedback.py:383-397` vs `dispatch.py:468-492`), runtime-json untouched, XSS-safe rendering via `pre()`/`h()` text nodes (`dashboard.html:938,1077`), briefing append-after-constraints with omission notices (`briefing.py:338-371`), README/CHANGELOG present. Exit gates 1–3 are covered by `tests/test_feedback.py`; gate 4 by `tests/test_factory.py:2397-2448`, `tests/test_briefing.py`, and the existing `tests/test_runtime.py` network-forbidden hook.

**Required**

1. **Full-snapshot cadence regression / rate-limit exhaustion** — Issue: "Preserve full-snapshot cadence and existing ticket/PR fields."
   - `dashboard.py:883-892` runs `feedback.collect` for every `n in raw_prs`; `raw_prs` includes closed and merged PRs (`dashboard.py:825-827` explicitly prefers the merged one). Nothing filters on `raw["state"]`, which the main query already returns.
   - Per PR, serial `gh` subprocesses: head ×2 (`feedback.py:169,339`), reviews ≥1 (`:236`), threads ≥1 (`:254`), check-runs + statuses (`:296-298`) → ≥6 process spawns + HTTPS round-trips, with a 30 s budget each (`:22,95`).
   - `snapshot()` executes under `_cache_lock` with `SNAPSHOT_TTL=15` (`dashboard.py:84,962-967`); client auto-refresh is 30 s (`dashboard.html:1650`), and `/api/act` previews force `fresh=1` (`dashboard.html:1417`).
   - Impact: on a repository with tens of `agent/<n>` PRs in its newest 100, every refresh adds hundreds of `gh` calls and tens of seconds to minutes of latency behind the lock. `THREAD_QUERY` requests `reviewThreads(first:50){comments(first:100)}` (`feedback.py:34-35`), ~50 GraphQL points per page [INFERENCE: GitHub's node-count cost formula]; ~30 PRs ≈ 1.5k points per snapshot against a 5 000/hr budget, after which the pre-existing `github()` GraphQL query fails and the dashboard loses all issues/PRs (`dashboard.py:828-829`). That breaks existing consumers, not just the new field.
   - Minimum fix: collect only for `raw["state"] == "OPEN"` and, for non-open PRs, emit the schema-1 object with all coverage `unavailable` and a stable reason (the spec explicitly permits unavailable coverage with retained facts); or otherwise bound total collector calls per snapshot.

**Optional**

- `coverage.pr` is marked `partial` and an `errors[]` row appended for `ownership_unverified` (`feedback.py:372-373`) even when the PR read was complete. Coverage describes read completeness; ownership is already carried in `owner.relation`. Conservative, but it makes "complete read, unverified owner" indistinguishable from a real partial read.
- Exit gate 4 "`<img onerror=…>` renders literally" has no automated check; repo has no JS harness, so this rests on the browser smoke in the handoff. Not blocking.

VERDICT: REVISE

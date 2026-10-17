## Standards

No `AGENTS.md`/`CONTRIBUTING.md` in the repo; `pyproject.toml:36-37` sets `[tool.ruff] line-length = 100` but nothing enforces it and `main` already violates it widely (dashboard.py 25 lines, briefing.py 55), so not a rule. CI (`.github/workflows/ci.yml:12`) runs only `unittest discover`; gate PASS.

**Hard violations:** none.

**Judgement calls (non-blocking):**

- **Quote style divergence** — `factory/feedback.py` is single-quoted throughout (582 `'…'` vs 9 `"…"`); every sibling module is double-quoted (`dashboard.py` 936/6, `lifecycle.py` 658/0, `runtime_events.py` 678/0). Global AGENTS.md §3 "Match existing style". Cosmetic; no tooling enforces it.
- **Possible Duplicated Code / second reader** — `factory/dashboard.py:850-873` re-parses the `events.jsonl` byte-tail (drop cut first line, drop unfinished last line, per-line `json.loads`) while `rows` from `lifecycle.journal_snapshot` at `dashboard.py:837` is the committed journal already in scope in the same function, and `briefing.py:244-254` has the same tail-parse shape. Reusing `rows` would delete the block and the extra unlocked read; the author chose the 2 MB bound deliberately (README diff line 78), which the ticket permits ("accepted bounded lifecycle/event readers"). Leave unless the duplicate read bites.
- `feedback.py:412` uses `lifecycle._lifecycle` (private). Established pattern (`runtime_events.py:171`, `gate.py:184`); not new.

## Spec (issue #79 + brief comment)

Schema-1 keys, enums, nullability, canonicalization and bounds match the normative text:

- `source_revision` field set `feedback.py:33-35, 250` = the 14 listed fields exactly; `observation_id` `feedback.py:444-446` = repo/PR native IDs, final head, sorted `(evidence_id, source_revision)`, coverage `status/truncated/reason`, excludes `observed_at`.
- Bounds `feedback.py:24-28` (100/2/20000/32/30 s); initial+final head reads `feedback.py:267, 375`; race → all sources partial with both heads, relevance unknown `feedback.py:382-386, 431`.
- Factory review requires accepted lifecycle `result` with `returncode==0`, `parsed`, `head==actual_head`, bracketing `enter`/`exit` with outcome `approved|product_feedback` `feedback.py:418-424` — mirrors `dispatch.py:468-477` acceptance; VERDICT prose never promoted (`pr.verdicts` untouched, legacy-labelled in `briefing.py:38`, `dashboard.html:124-128`).
- Runtime-JSON path untouched (`dashboard.py:1228-1235` does not call `snapshot()`); non-PR tickets get no feedback (`dashboard.py:884`).
- Review drawer/Inbox render body via `pre()` text nodes and `safeSourceURL` (`dashboard.html:104-105, 38-40`); no new action control.
- `sources_for` appends feedback after existing candidates and reports omission/shortening (`briefing.py:47-48, 110-122, 130-138`); missing key / wrong version → explicit unsupported notice (`briefing.py:51-68`).
- README/CHANGELOG document schema location, bounds, unknown/partial, read-only authority.

**Scope deviation (documented, not approved in comments):** `collect_details=raw.get("state") == "OPEN"` at `dashboard.py:895` — merged/closed PRs get the schema-1 envelope with every source `unavailable`/`not_collected` and no provider read (`feedback.py:30-32, 134-137`). The issue text never requires detail reads for non-open PRs and does require "preserve full-snapshot cadence"; the envelope still satisfies "schema-1 object with explicit unavailable coverage". Acceptable; flagged so the handoff states it.

### Required fixes

None.

### Optional suggestions

1. `feedback.py:143-144` — after a read returns successfully, `time.monotonic() > deadline` raises `TimeoutError` and discards the returned data. Trigger: a read that completes a few ms past the deadline. Impact: an observed fact dropped as `timeout`. The subprocess `timeout=remaining` already bounds the wait; the post-check could just let the value through.
2. `feedback.py:437-439` — `review_comment` with `thread_outdated=false` and `line=null` (GitHub file-level comments) is forced to `unknown` with a `threads` `partial` gap even when `source_head_sha == final_sha`. Spec only mandates `unknown` for missing/contradictory SHA or race; this is stricter than required. Harmless (conservative).
3. `feedback.py:408-409` — `ownership_unverified` is recorded as a `pr` coverage gap, so `pr.status` is `partial` for every PR without a verified closing-issue link, conflating ownership with read coverage. Consumers can still read `owner.relation`; consider recording it only in `owner.evidence`.
4. `dashboard.py:884-896` — collection is serial per open agent PR inside `snapshot()`: ≥5 `gh` subprocesses and a 30 s ceiling each. With N open PRs refresh latency is O(N·30 s) worst case. Constants are spec-mandated; just note for the handoff.
5. `dashboard.py:850-873` — see Standards: replace with `rows` from `journal_snapshot` if the second read of `events.jsonl` ever matters.

Summary — Standards: 0 hard, 3 judgement calls (worst: duplicated event-tail reader). Spec: 0 missing/wrong requirements, 1 documented scope narrowing (open-PR-only detail reads), 4 non-blocking edge notes.

VERDICT: APPROVE

## Review: #79 (B2 source-versioned PR feedback)

### Standards
No `AGENTS.md`/`CONTRIBUTING.md` in the snapshot; `docs/` holds plan documents, not conventions. Observable conventions followed: `unittest` suite (`tests/test_feedback.py:1218-1497`), README/CHANGELOG updated (`README.md:21-93`, `CHANGELOG.md:9`), text-only DOM via `h()`/`pre()` (`dashboard.html:350`, `pre` is a text node at snapshot `dashboard.html:938`), private `lifecycle._lifecycle` use has precedent (`lifecycle._now` already used in `dashboard.py`).

### Spec (issue #79 body + agent brief)
Verified against the diff:
- Schema-1 envelope and item fields, all required keys, explicit nulls: `feedback.py:664-670`, `779-788`.
- `source_revision` over exactly the 14 named fields: `feedback.py:605-607`, `811`. `observation_id` over native IDs, final head, sorted `(evidence_id, source_revision)`, coverage status/truncated/reason, excluding `observed_at`: `feedback.py:986-988`.
- Bounds: `ITEM_LIMIT=100`, `PAGE_LIMIT=2`, `BODY_LIMIT=20_000`, `ERROR_LIMIT=32`, `DETAIL_TIMEOUT=30`, initial/final head reads: `feedback.py:599-603`, `750`, `920`; timeout enforced through transport `timeout` → `subprocess.run(timeout=)` (`dashboard.py:495`).
- Head race → relevance `unknown`, both heads in reason, facts retained: `feedback.py:927-931`, `973-981`. Missing source SHA never filled with head: `774-776`. `thread_outdated=true` → historical regardless of SHA: `977-978`.
- Factory review requires accepted lifecycle `result` (returncode 0, parsed, head == actual_head, matching enter/exit outcome), not `VERDICT` prose: `feedback.py:954-968`; matches the real emitter (`dispatch.py:489-491`, `477`).
- Ownership requires same-repo closing-issue link (id+url) **and** retained `claimed` event: `feedback.py:937-945`; matches `dispatch.py:1297`.
- Error sanitization: `gap()` emits fixed strings only (`feedback.py:680-688`); the test asserts no `SECRET` leaks (`test_feedback.py:1373`).
- Full-snapshot-only integration at `tickets[].pr.feedback` (`dashboard.py:563-572`); runtime-json branch untouched (snapshot `dashboard.py:1224-1250`); legacy `checks/comments/verdicts/review_decision/gate_text` untouched (`pr_record` unchanged, asserted `test_factory.py:1200-1204`).
- `sources_for`: feedback appended after existing candidates, omissions/shortening counted and reported, missing/unknown schema reported as unknown: `briefing.py:136-187`, `191-203`, `211-219`.
- Review drawer / Inbox / task facts render coverage, heads, relevance, disposition, location, links via `safeSourceURL`: `dashboard.html:271-360`, `395-407`, `415`.

No acceptance criterion found unmet in the diff.

### Plan drift
Plan not recorded; compared against the issue's "Touches" list. Files changed: `CHANGELOG.md`, `README.md`, `factory/briefing.py`, `factory/dashboard.html`, `factory/dashboard.py`, `factory/feedback.py`, `tests/test_briefing.py`, `tests/test_factory.py`, `tests/test_feedback.py` — all named by the issue (`test_feedback.py` conditionally permitted). No unnamed files.

Planned proof missing from the gate report: exit gate 5 (disposable-repo `python -m factory dashboard --json` + live server/browser inspection of Review drawer and Inbox, `sources_for` bundle) and exit gate 7 (handoff packet with sanitized schema-1 example). The gate report covers only conflict-markers/test/leak-scan. The `<img onerror=...>` literal-render criterion (exit gate 4) is browser-only and not covered by the unit suite. Not blocking on its own; no defect exposed.

### Required fixes
None.

### Optional suggestions
1. `dashboard.py:497-499` — `github()` now raises `RuntimeError("GitHub read failed")` for the main full-snapshot GraphQL read too, so `snapshot()["errors"]` loses `gh` stderr (auth/404 diagnostics) for existing consumers. Sanitization is only required for feedback `errors`, which `gap()` already achieves without using exception text. Consider keeping stderr for the default (no `endpoint`/`query`) path.
2. `dashboard.py:563-572` — `collect()` runs serially per selected PR inside `snapshot()`, which `cached_snapshot()` executes synchronously under a lock on request. Worst case adds ~6 `gh` subprocesses and up to 30 s per PR to `/api/snapshot` latency. Within spec bounds; note as an operational risk.
3. `feedback.py:950-951` — unverified ownership marks `coverage.pr` as `partial` although the PR read itself completed; `owner.relation` already carries this. Conflates read completeness with ownership state in the drawer's coverage row (`dashboard.html:313-318`).
4. `test_feedback.py:1410-1415` — the pagination case's `Provider` ignores the `page` parameter and returns the same 100 rows for both pages, so the cap is exercised via dedupe rather than two distinct pages. Weak evidence for the two-page bound.
5. `dashboard.html:384` — "Legacy local gate failures" relabels the current local gate report as legacy; the gate is not a legacy mechanism. Wording only.
6. `dashboard.py:875-878` — `git status --porcelain -- .` scopes cleanliness to the `factory/` package directory, not the whole checkout; README claims a "clean source-checkout" revision (`README.md:85`).

VERDICT: APPROVE

Note on inputs: `./review.diff` and `./snapshot` were absent from the sandbox. I reviewed `git diff 8fba9c5..HEAD` (the issue's stated base revision → gated head `7bd5805`, 9 files, +1249/−38) and the checked-out tree as the snapshot. Line references below are `path:line` in that diff output (`../review79-before.diff`) with the target source line where useful. The repo has no AGENTS.md/CONTRIBUTING.md; standards sources are README.md (`python -m unittest discover -s tests` gate) and pyproject (ruff line-length 100, not CI-enforced). GH issue #79 fetched via web.

## Required fixes

### R1. `github()` now fails the whole full snapshot on GraphQL partial errors and drops diagnostic text — regression in a path the issue says to preserve
- `review.diff:496-505` (`factory/dashboard.py` `github()`): `if value.get("errors"): raise RuntimeError("GitHub query was incomplete")` applies to the **default full-snapshot query**, not just feedback reads. Base code returned `json.loads(proc.stdout)["data"]` and consumed partial data.
- Trigger: GitHub GraphQL returns `data` + `errors` for nullable sub-fields routinely — the default query (`factory/dashboard.py:114-117`, `:130-132`) requests `upstream: repository(...)` (NOT_FOUND when the configured upstream is inaccessible) and `CROSS_REFERENCED_EVENT` timeline nodes (FORBIDDEN when an issue is cross-referenced from a private repo). Both previously yielded a usable snapshot with a `null` node.
- Impact: `snapshot()` catches `RuntimeError` at `factory/dashboard.py:828-829`, leaving `issues=[]`, `prs={}` → `tickets: []` and `errors: ["github: GitHub query was incomplete"]`. Every GitHub-backed ticket disappears from the dashboard, Inbox, and `--json`.
- Same hunk, `review.diff:499`: `raise RuntimeError("GitHub read failed")` replaces `proc.stderr.strip() or ...`, so the existing snapshot `errors` surface loses `gh` diagnostics (e.g. "not logged in"). Sanitization is unnecessary here for the collector: `factory/feedback.py` already discards exception text (`review.diff:706-709` maps every exception to fixed strings).
- Rule: issue #79 Scope — "Preserve the existing `pr.checks`, `pr.comments`, `pr.verdicts`, `review_decision`, gate text, and current snapshot fields for their existing consumers"; Integration — "Preserve full-snapshot cadence and existing ticket/PR fields." Also "reuse the existing full-snapshot GitHub transport" — reuse, not change its failure semantics.
- Fix (minimal): keep the base behavior for `query is None` (return `value["data"]`, propagate stderr text); only the feedback `endpoint`/explicit-`query` branch needs the stricter handling, and even there the collector's `head()`/threads code already treats `None`/malformed data as `malformed_response`, so the `errors` check can simply be dropped.

## Optional suggestions (non-blocking)

- `review.diff:530-553` (`dashboard.py` provenance): `snapshot()` already holds a coherent committed journal view — `rows` from `lifecycle.journal_snapshot(dispatch.EVENTS)` at `factory/dashboard.py:837`. The diff adds a second, independent 2 MB tail read of the same `events.jsonl` with its own torn-line/uncommitted logic (the subject of commit `7bd5805`). Passing `rows` as `events` removes ~25 lines and a second reader over a file that is being appended. Issue text favours this ("reuse ... accepted bounded lifecycle/event readers for provenance"); not a correctness defect as written.
- `review.diff:954` (`feedback.py`): `lifecycle._lifecycle(e)` imports a private validator; `runtime_events` also re-validates lifecycle rows. If `rows` from `journal_snapshot` are used (above), consider whether a public accessor exists before coupling to `_lifecycle`.
- `review.diff:383-384, 390-391` (`dashboard.html` `taskFacts`): the local gate report (`t.gate`) is relabelled "Legacy local gate…". The repository gate is current, deterministic evidence (README gate section), not legacy; the wording conflates it with provenance-free PR verdict comments. Wording only.
- `review.diff:839-841`: REST reviews carry `submitted_at`, not `updated_at`, so `source_updated_at` is always `null` for `review` items; README `review.diff:57-58` documents nulls for check runs but not reviews. Per spec ("when supported") null is acceptable; document it alongside the check-run note.
- `review.diff:366-375` (`tabReview`): legacy verdict chips lose their `PASS`/`FAIL` classes. Cosmetic; existing `pr.verdicts` data is preserved, which is what the spec requires.

## Spec coverage (issue #79 exit gate vs diff)

- Gate 1 ✓ `tests/test_feedback.py` `review.diff:1272-1309`, `tests/test_factory.py` `:1157-1207` (simultaneous review/review_comment/check_run/factory_review; provenance-free `VERDICT` excluded).
- Gate 2 ✓ `review.diff:1311-1342` (reorder + timestamp → identical ids; body/state/line/resolved/conclusion/attempt → only that `source_revision` changes).
- Gate 3 ✓ `review.diff:1344-1485` (same/advanced/raced/missing head, dismissed, resolved/outdated, check states, empty-complete, pagination, body cap, timeout, malformed, one-source failure).
- Gate 4 partial: legacy fields ✓ (`:1198-1204`), older-engine no-key ✓ in briefing (`:1116-1125`), `sources_for` ordering/omission ✓ (`:1132-1143`), non-PR ticket ✓ (`:1207`), runtime-json — relies on pre-existing `tests/test_runtime.py` audit harness (acceptable). `<img onerror=...>` literal rendering has no automated check; DOM path verified by inspection: `h()` uses `createTextNode` (`factory/dashboard.html:464`), `pre()`/`safeSourceURL` reused (`review.diff:286-289, 350`).
- Gates 5–7 (CLI/browser smoke, handoff packet) are not in the gate report (only conflict-markers/test/leak-scan). Issue comment shows a truncated sanitized schema-1 example from the worker handoff, so gate 7 material exists; gate 5 browser observations are not evidenced in-diff. Not a code defect.

## Standards

- Gate command unchanged; new tests use `unittest` per README ✓.
- Ruff `line-length = 100` (pyproject) is not CI-enforced; `feedback.py` has 63 lines >100 vs 26 pre-existing in `dashboard.py`. Tooling-level; not reported as a violation.

## Plan drift

Plan status not recorded → no plan file list to diff against. Files changed match the issue's "Touches" list exactly (`feedback.py`, `dashboard.py`, `briefing.py`, `dashboard.html`, `test_factory.py`, `test_briefing.py`, `test_feedback.py`, `README.md`, `CHANGELOG.md`); no out-of-scope files (`dispatch.py`, `manage.py`, `triage.py`, `lifecycle.py`) touched. Missing planned proof: exit gate 5 smoke/browser evidence absent from the gate report. Drift exposes no defect beyond R1.

VERDICT: REVISE

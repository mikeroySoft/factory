## Standards

No `AGENTS.md`/`CONTRIBUTING.md` in the snapshot. Documented conventions found: CI gate = `python -m unittest discover -s tests` (`.github/workflows/ci.yml:12`), `[tool.ruff] line-length = 100` (`pyproject.toml:36-37`, not enforced by CI), "Keep the normal Python package dependency-free" (`docs/c1-read-interface-handoff.md:104`). Gate PASS; no new dependency (`feedback.py` imports stdlib + `factory.lifecycle`, review.diff:588-597). No hard documented-standard violation.

Judgement-call smells (non-blocking):
- **Duplicated Code** — `snapshot()` already holds a coherent committed journal view (`rows` from `lifecycle.journal_snapshot`, dashboard.py:837-844) yet the diff re-reads `events.jsonl` via `briefing.bounded_file(..., tail=True)` and hand-parses lines (review.diff:530-553). The issue asked to reuse "accepted bounded lifecycle/event readers"; `rows` is that reader and is already in scope.
- **Reaching into a private** — `lifecycle._lifecycle(e)` (review.diff:954) is underscore-private and only used inside `lifecycle.py` today (lifecycle.py:597,690,789).
- Long lines >100 in `feedback.py` (review.diff:779, 907, 943) and `dashboard.py` (review.diff:566-567) vs `pyproject.toml` ruff config; existing code already exceeds, lint isn't gated.

## Spec (issue #79 + agent brief)

Satisfied, with evidence:
- Single collector `factory/feedback.py`, envelope keys/enums/nullability as specified (review.diff:664-670, 779-788); fixed constants (599-603); two head reads bracketing the detail group (750, 920); head race → all coverage partial with both SHAs, relevance unknown (927-931, 973); final-head failure keeps facts, null head (920-926, test 1462-1471).
- `source_revision` over exactly the listed fields (605-607, 811); `observation_id` over native IDs + head + sorted pairs + coverage status/truncated/reason, excluding `observed_at` (986-988); items sorted by `(evidence_id, source_revision)` (984).
- Factory review requires validated lifecycle `review`/`result` row with `returncode==0`, `parsed is True`, `head==actual_head`, bracketing enter/exit with matching outcome (954-968) — matches `dispatch.py:468-492`. Provenance-free `VERDICT` rejected (test 1304-1309).
- Ownership requires same-repo closing-issue link (id+url match) plus retained `claimed` event (937-947); `claimed` shape matches `dispatch.py:1297`.
- Full snapshot only; `--runtime-json` path untouched (dashboard.py:1227-1231 doesn't call `snapshot()`); non-PR tickets get no feedback (563-572; test 1207).
- Briefing: feedback appended after existing candidates; omissions/shortening reported; unsupported/unknown schema notices (136-187, 211-228); unsupported schema contents not interpreted (test 1126).
- Dashboard: body via `pre()`/text nodes only (350, `h()` uses `createTextNode`, dashboard.html:464); links through `safeSourceURL` (286-289); unsupported/missing/non-PR states rendered as unknown (273-283).
- README/CHANGELOG document schema location, bounds, unknown/partial, read-only authority (9, 21-93).

Defect found — see Required.

## Plan drift

Plan not recorded; using the issue "Touches" list as the plan. Files changed: `CHANGELOG.md`, `README.md`, `factory/briefing.py`, `factory/dashboard.html`, `factory/dashboard.py`, `factory/feedback.py`, `tests/test_briefing.py`, `tests/test_factory.py`, `tests/test_feedback.py`. All named; `tests/test_feedback.py` is the conditionally-allowed file. No unplanned files.

Planned proof missing from the gate report: exit-gate items 5 (disposable-repo `python -m factory dashboard --json` + real-browser Review drawer/Inbox check + `sources_for` bundle inspection, with recorded commands/observations) and 7 (handoff with sanitized full-snapshot example). The gate report covers only conflict-markers/test/leak-scan. Issue comment 2 contains a partial sanitized snapshot from an earlier REVISE'd attempt, not evidence for this head. Not blocking on its own; no defect exposed beyond the one below.

## Required fixes

1. **`producer.revision` can report an unrelated repository's HEAD.** `review.diff:555-558`: `source_root = Path(__file__).resolve().parent`, then `git rev-parse HEAD` and `git status --porcelain -- .` with that cwd. When Factory is installed (non-editable) into a venv that lives inside any git checkout — e.g. `<target-repo>/.venv/lib/python3.x/site-packages/factory/`, the common `uv`/in-project-venv layout — `git rev-parse HEAD` resolves the *enclosing* repo's HEAD and `git status --porcelain -- .` is empty because `.venv` is ignored, so `revision` becomes the target repo's SHA and `sha()` accepts it (review.diff:664). Impact: a hex SHA that is not the Factory source/build revision is emitted as `producer.revision` and surfaces in the Review drawer (`review.diff:301`), briefing payloads (`review.diff:159-167`), and the #15 handoff. Violates issue #79 "Normative schema 1 → `producer.revision` is a verifiable source/build revision when available, otherwise null" and the diff's own README claim "The producer revision is a clean source-checkout Git revision, otherwise null" (`review.diff:85-86`). Minimal fix: only trust the SHA when the package file is tracked by that repo, e.g. gate on `sh(["git", "ls-files", "--error-unmatch", "__init__.py"], cwd=source_root)` being non-empty (`sh` already returns `""` on nonzero, dashboard.py:169-171); otherwise `revision = None`.

## Optional suggestions

- Replace the second journal read (`review.diff:530-553`) with the `rows` already loaded by `lifecycle.journal_snapshot` (dashboard.py:837); `provenance_complete` then reflects the same committed view the rest of the snapshot uses. Smaller diff, no 2 MB tail edge cases.
- `github()` now raises `RuntimeError("GitHub read failed")` for the pre-existing full-snapshot query too (`review.diff:499`), so the dashboard's `errors: ["github: ..."]` (dashboard.py:829) loses the `gh` stderr (`Not logged in`, rate-limit text) operators relied on. Sanitize only the feedback-supplied reads (`endpoint`/`query` given) and keep stderr for the default query.
- `item()` leaves `counts[source]` incremented after removing a conflicting item (`review.diff:816-824`), so a source with a conflict can retain 99 items under a 100 cap. Cosmetic vs the stated bound.
- Full snapshot now serializes ~5–7 `gh` subprocesses per selected PR under `_cache_lock` (`review.diff:565`, `SNAPSHOT_TTL = 15`, dashboard.py:84, 963-967). With tens of open agent PRs a snapshot can exceed its TTL. Requested by the issue, so not a defect — but worth recording in the handoff as the observed cost.
- `lifecycle._lifecycle` is private; exposing a thin public alias in `lifecycle.py` is out of scope for #79 ("No changes to factory/lifecycle.py"), so leave as-is and note it for the #15 owner.

VERDICT: REVISE

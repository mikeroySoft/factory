## Standards

No `AGENTS.md`/`CONTRIBUTING.md` in the snapshot; the only documented conventions are the issue template (Scope/Touches/Exit gate/Out of scope) and README. The diff edits only files the issue's **Touches** names (incl. the conditionally allowed `tests/test_feedback.py`). Docs updated (`README.md:21-93`, `CHANGELOG.md:9`). No hard violations.

Smell (judgement call, non-blocking): `feedback.collect` is one ~340-line function with six nested closures (`factory/feedback.py:71-408`, review.diff:652-989). Readable enough with the section comments; mention only.

## Spec (issue #79 + triage brief)

Satisfied, with evidence:
- Schema-1 envelope, enums, nullability, sorted `items`/`errors`, `source_revision` over exactly the 14 listed fields, `observation_id` over native IDs + final head + `(evidence_id, source_revision)` + coverage status/truncated/reason (`feedback.py:24-26, 230, 403-407`).
- Fixed bounds, initial/final head reads, race → all sources partial with both heads, final-head failure → null head + `unknown` relevance, no promotion (`feedback.py:18-22, 339-350, 389-400`); tests cover these (`tests/test_feedback.py:127-148, 245-254`).
- Ownership needs same-repo closing-issue link + retained `claimed` row; `VERDICT` prose never becomes `factory_review`; Factory review requires valid lifecycle `result` with `returncode==0`, `parsed`, `head==actual_head`, bracketing enter/exit (`feedback.py:352-387`), matching the real emitter in `dispatch.py:489-492`.
- Full-snapshot-only integration; `--runtime-json` branch (`dashboard.py:1224-1250`) never touches `snapshot()`/`feedback`. Non-PR tickets get no object (`test_factory.py:1207`).
- Briefing appends after human constraints, reports omissions/shortening, coverage notice for missing/unsupported/partial (`briefing.py` hunks, review.diff:136-246; tests review.diff:1078-1143).
- UI renders bodies via `pre()`/text nodes only (`dashboard.html:938`, `h()` at 453-466 creates text nodes; `dataset`/`open:false`/null kids handled), links via `safeSourceURL`.

### Required fixes

1. **Aggregate collection cost breaks the preserved full-snapshot cadence** — `factory/dashboard.py:882-891` (review.diff:563-572). `feedback.collect` runs for *every* selected issue whose number appears in `prs` — including merged/closed PRs from the latest-100 window — on every `snapshot()`. Each call issues ≥6 serial `gh` subprocesses (2× `HEAD_QUERY`, threads, reviews, check-runs, statuses: `feedback.py:169, 246-247, 268, 310-313, 339`) with a 30 s per-PR deadline. `SNAPSHOT_TTL = 15` (`dashboard.py:84`) and the UI auto-reloads every 30 s (`dashboard.html:1650`); `cached_snapshot` recomputes under `_cache_lock` (`dashboard.py:962-967`). Trigger: an active repo with a few dozen Factory PRs in the window → each snapshot takes minutes (worst case 100 × 30 s), every poll re-collects because TTL already expired, `/api/snapshot?fresh=1` and `--json` block for the same duration, and the extra REST/GraphQL volume consumes the shared rate limit that the pre-existing full GraphQL query depends on. Issue text: *"Preserve full-snapshot cadence and existing ticket/PR fields"* and *"Preserve ... current snapshot fields for their existing consumers."* The per-PR bounds are spec'd; the unbounded N× fan-out on a 15 s cache is not. Minimal fix options (worker's choice): restrict collection to PRs still relevant to the read surface (e.g. `raw["state"] == "OPEN"` or non-terminal tickets), or reuse the previous cached ticket's feedback object when the single cheap head read shows an unchanged `headRefOid`. No new abstraction needed.

### Optional suggestions

- `factory/dashboard.py:218` (review.diff:499): `raise RuntimeError("GitHub read failed")` drops `gh` stderr from the existing `errors[] = "github: …"` snapshot diagnostic. The collector already replaces transport messages with fixed sanitized strings (`feedback.py:126-128`), so the transport can keep stderr for the legacy snapshot path without leaking into feedback.
- `factory/feedback.py:258` (review.diff:839): REST `pulls/{n}/reviews` objects have `submitted_at`, not `updated_at`; `source_updated_at` is therefore always `null` for `review` items and every truncated review body is tagged `change_detection_incomplete`. GraphQL `PullRequestReview.updatedAt` would honor "actual provider UTC update time when supported". Nullable per schema, so non-blocking.
- `factory/feedback.py:328-329`: `row.get('html_url', row.get('target_url'))` is fine today; a check-run with explicit `html_url: null` yields null rather than falling back — harmless, noted.

## Plan drift

Plan not recorded. All nine changed files are named in the issue Touches. Gate report shows conflict-markers/test/leak-scan only; exit-gate items 5 (disposable-repo `--json` + live browser + `sources_for` inspection with recorded commands) and 7 (sanitized schema-1 handoff example) are handoff evidence not visible in the diff or gate report — unverified here, not a defect of the diff.

VERDICT: REVISE

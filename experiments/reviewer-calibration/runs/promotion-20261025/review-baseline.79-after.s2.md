## Standards

No `AGENTS.md`/`CONTRIBUTING.md`/`docs/` coding-standards file exists in the snapshot; the only documented repo rules are CI = `python -m unittest discover -s tests` (`.github/workflows/ci.yml:12`, gate PASS) and `[tool.ruff] line-length = 100` in `pyproject.toml:36-37` (not enforced by CI; existing `dashboard.py` already exceeds it). README/CHANGELOG updated as the issue's "Touches" require (`review.diff:9`, `review.diff:21-105`).

Safe-rendering convention held: every feedback string goes through `h()` text nodes / `pre()` (`factory/dashboard.html`, `review.diff:381`, `review.diff:338`), link hrefs through `safeSourceURL` (`review.diff:314-317`).

Smell notes (judgement calls, non-blocking):
- `collect()` is a ~360-line function with five nested closures (`factory/feedback.py:79-441`, `review.diff:696-1058`). Readable because the closures share `result`/`reasons`, but a long function nonetheless.
- Cross-module private import `lifecycle._lifecycle` (`review.diff:1023`); it is re-run over the full journal for every open PR per snapshot (`review.diff:600-608` calls `collect` per PR, `review.diff:1023` revalidates all rows each time). Validate once in `snapshot()` and pass validated rows if this shows up in refresh time.

## Spec (issue #79 + agent brief)

Implemented and test-backed:
- Schema-1 envelope, required keys, enums, explicit nulls (`review.diff:711-719`, `829-838`); `items` sorted by `(evidence_id, source_revision)` and `errors` sorted/bounded (`review.diff:1053-1054`, `736`).
- `source_revision` over exactly the 14 listed fields (`review.diff:644-646`, `861`); `observation_id` over native IDs + final head + sorted pairs + coverage status/truncated/reason, excluding timestamps (`review.diff:1055-1057`); idempotent reorder/timestamp polls and semantic revisions tested (`review.diff:1499-1530`).
- Bounds: `ITEM_LIMIT=100`, `PAGE_LIMIT=2`, `BODY_LIMIT=20000`, `ERROR_LIMIT=32`, `DETAIL_TIMEOUT=30`, initial+final head read (`review.diff:635-639`, `878`, `986`); head race → partial + both SHAs + `unknown` relevance (`review.diff:993-997`, `1042`); final-head failure keeps facts and null head (`review.diff:1650-1659`).
- Factory review requires valid lifecycle `result` with `returncode==0`, `parsed`, `head==actual_head`, matching enter/exit outcome (`review.diff:1029-1035`); field names match what `dispatch.review()` actually emits (snapshot `factory/dispatch.py:489-492`). Provenance-free `VERDICT` rejected (`review.diff:1492-1497`).
- Ownership needs same-repo closing link + retained `claimed` event (`review.diff:1009`); Factory PRs do carry `Closes #n` (snapshot `dispatch.py:1319`) and `claimed` is recorded (`dispatch.py:1297`), so this is live in production.
- Full-snapshot only: `collect` wired in `snapshot()` (`review.diff:596-608`); `--runtime-json` branch returns before `snapshot()`; pre-existing audit hook in `tests/test_runtime.py:35-42` forbids any `gh` spawn on that path.
- Legacy `checks/comments/verdicts/review_decision/gate_text` preserved (`review.diff:1319-1323`).
- `sources_for`: feedback appended after human constraints, omission/shortening counted and reported, coverage notice distinguishes missing / unsupported / incomplete / `not_collected` (`review.diff:156-215`, `239-247`, `255-274`; tests `1147-1233`).
- Transport reuse: `github()` extended with `endpoint`/`query`/`variables`/`timeout` (`review.diff:510-537`); ints go via `-F`, strings via `-f`, `None` vars dropped.

Scope deviations, approved or neutral:
- `collect_details=False` for non-open PRs (`review.diff:607`) is not in the issue text but is a bounding choice consistent with "stop cleanly at each bound"; it returns the schema-1 envelope with `not_collected` (`review.diff:746-748`, test `1681-1706`) rather than an empty success, which satisfies "partial or unavailable never means none".
- Legacy-label copy changes in `taskFacts`/`tabReview` (`review.diff:401-406`, `415-425`) go slightly beyond "presentation only" but implement "do not redefine those legacy fields as source-versioned evidence".

## Plan drift

Plan status not recorded, so no file-level diff against a plan is possible. Files changed vs. issue "Touches": `CHANGELOG.md`, `README.md`, `factory/{briefing.py,dashboard.html,dashboard.py,feedback.py}`, `tests/{test_briefing.py,test_factory.py,test_feedback.py}` — all named in the issue (`test_feedback.py` explicitly allowed).

Proof missing from the gate report (not detectable from the diff; not a required defect in the code):
- Exit gate 5: disposable-repo `python -m factory dashboard --json` run, live server + real-browser Review drawer / Inbox check, literal `<img onerror=…>` rendering, absence-of-mutation log.
- Exit gate 7: sanitized real full-snapshot example for the #15 handoff (the issue comment shows one fragment from attempt 4, not for this revision).

## Required fixes

None.

## Optional suggestions

1. `factory/dashboard.py` (`review.diff:582-583`): a missing `.factory/events.jsonl` (`bounded_file` returns `None` on ENOENT) sets `provenance_complete=False`, so every open PR on a fresh repo gets `provenance_partial` and `reviews` can never be `complete` (`review.diff:1017-1018`). An absent journal is an observed-empty source, not a truncated one; treat `None` as complete-empty.
2. `factory/feedback.py:402-403` (`review.diff:1019-1020`): `ownership_unverified` is recorded as a `pr` coverage gap, forcing `pr.status = partial` even when the PR read itself was complete. Coverage is meant to describe read completeness; the UI then shows `pr` as PENDING (`review.diff:343`). Keep it in `errors`/`owner.evidence` without downgrading `pr` coverage.
3. `factory/feedback.py:356` (`review.diff:973`): REST check runs expose `app`, not `creator`, so check-run `author` is always `{login:null, kind:unknown}`; `app.slug` would give evidence-backed `bot` attribution as it already does for statuses.
4. `factory/feedback.py` (`review.diff:829`, `971`, `1009`, `1011`): lines well past the `pyproject.toml` ruff `line-length = 100`. Not CI-enforced; mention only.

VERDICT: APPROVE

## Standards

No `AGENTS.md`/`CONTRIBUTING.md` in the repo; documented conventions are the CI gate (`python -m unittest discover -s tests`, `.github/workflows/ci.yml:12`) and `CHANGELOG.md` `Unreleased` entries suffixed `(#N)`. Diff complies: `CHANGELOG.md:9`, `README.md:21-105` (schema location, fixed bounds, unknown/partial, read-only authority all documented as the issue's Touches section requires).

Observations (judgement calls, non-blocking):
- `factory/dashboard.py:853-873` (diff L565-585) hand-rolls the committed-JSONL tail parse that `factory/evidence.py:895-910` already does identically. Existing repo pattern, so not a violation; Duplicated Code smell only.
- `factory/feedback.py:406` (diff L1023) calls `lifecycle._lifecycle`; cross-module private use has precedent (`runtime_events.py:171`, `gate.py:184`).
- `factory/feedback.py` uses single-quoted strings while the rest of `factory/` uses double. No documented rule; not reported as a finding.

## Spec (issue #79)

Required keys/enums/nullability, canonicalization, `source_revision` field set (`feedback.py:27-29`, diff L644-646), `observation_id` inputs (`feedback.py:438-440`, diff L1055-1057), item sort, error bound/sort, fixed constants (`feedback.py:18-22`, diff L635-639), initial/final head reads with race handling (`feedback.py:369-380`, diff L986-997), one-source-failure isolation, no cache/mutation/model: all present and exercised in `tests/test_feedback.py`. Runtime-JSON path untouched (`dashboard.py:1228-1254` never calls `snapshot()`); existing `tests/test_runtime.py:48-50` audit already proves no commands. Body rendered only via `pre()`/`h()` text nodes (`dashboard.html:1075`, diff L381; helper at `dashboard.html:453-466`). Legacy `checks/comments/verdicts/review_decision/gate_text` preserved and asserted (`tests/test_factory.py` diff L1319-1323). Briefing appends feedback after human constraints and reports omission (`briefing.py` diff L156-247; `tests/test_briefing.py` diff L1222-1233).

No required fixes found.

### Optional suggestions
1. `factory/feedback.py:355-357` (diff L972-974): check-run REST objects carry no `updated_at`, `run_attempt`, or `creator` (they have `started_at`/`completed_at`/`app`), so for `check_run` items `source_updated_at`, `run_attempt`, and `author.login` are always null and any >20 KB output always triggers `change_detection_incomplete`. README already documents the nulls, so this is spec-compliant; `completed_at` / `app.slug` would give a real signal and `bot` attribution if wanted later.
2. `factory/dashboard.py:888-896` (diff L600-608): detail collection is synchronous inside `snapshot()`, which `/api/snapshot` runs behind a 15 s TTL (`dashboard.py:84`, `968-971`). Worst case is `DETAIL_TIMEOUT` × open agent PRs per refresh. Issue mandates these reads and open-only scoping is applied (`collect_details=raw.get("state") == "OPEN"`), so not a defect; worth noting the latency ceiling in README.
3. `factory/briefing.py:399-407` (diff L265-274): the coverage source's `truncated` flag now reflects only budget/ledger/timeline cuts, not partial/unavailable feedback coverage. `evidence.py:1126` uses that flag for its "cited sources are truncated" notice, so a partial-coverage feedback observation no longer surfaces there. Content is still in the notice text; flag if you want the structural signal too.

## Plan drift

- Plan status: not recorded, so no planned file list exists. Changed files (`CHANGELOG.md`, `README.md`, `factory/{briefing.py,dashboard.html,dashboard.py,feedback.py}`, `tests/{test_briefing.py,test_factory.py,test_feedback.py}`) match the issue's Touches list exactly; `tests/test_feedback.py` is the conditionally permitted dedicated file.
- Proof not in the gate report: exit-gate item 5 (disposable-repo `--json` run, live server + real-browser Review drawer/Inbox check, recorded absence of mutation calls) and item 7 handoff. The issue's handoff comment contains a sanitized schema-1 snapshot excerpt (historical review, current-head `review_comment` with `<img onerror=…>` body, `comment_page_limit` error) but it is truncated and shows no browser observations or `check_run` item. Gate report covers conflict-markers/test/leak-scan only. No code defect is exposed by this gap; it is a handoff-evidence omission for the human merger to confirm.

VERDICT: APPROVE

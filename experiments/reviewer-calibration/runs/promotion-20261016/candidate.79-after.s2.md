# Review: `origin/main..HEAD` vs issue #79 (B2 PR feedback)

Scope: 7 commits, 9 files. Repo has no `AGENTS.md`/`CONTRIBUTING.md`; documented conventions found: stdlib-only package (`docs/c1-read-interface-handoff.md:104`, `pyproject.toml:12 dependencies = []`), gate command `python -m unittest discover -s tests` (`.github/workflows/ci.yml:12`). Gate report is authoritative for build/test/scan.

## Standards

**Documented rules**
- Dependency-free: holds. `factory/feedback.py:7-16` imports only stdlib + `factory.lifecycle`.
- Gate: PASS per report.

**Smell baseline (judgement calls, non-blocking)**
- *Duplicated Code / "already in this codebase"*: `factory/dashboard.py:850-873` re-reads and re-parses `events.jsonl` via `briefing.bounded_file` while `snapshot()` already holds the locked, committed journal `rows` from `lifecycle.journal_snapshot` at `factory/dashboard.py:837`. Same parse shape also exists at `factory/evidence.py:895-912` and `factory/briefing.py:244-254`. Net effect: a second, unlocked 2 MB read of the same file per snapshot; an in-flight append between the two reads yields a spurious `provenance_partial` (`factory/feedback.py:400-401`). Passing `rows` would delete ~22 lines. Not a rule violation; the spec names "bounded" readers and `bounded_file` is one.
- *Duplicated Code*: reviews and threads pagination loops at `factory/feedback.py:264-292` and `:295-325` share shape. Judgement call; inlining is readable.
- *Feature Envy / private reach*: `factory/feedback.py:406` imports `lifecycle._lifecycle` (underscore-private) across modules. Other consumers use public `lifecycle.read_events`/`observe`. Consider exporting a public validator; not a documented rule.
- `tests/test_factory.py:2410,2482` import fixtures from `tests/test_feedback.py`; test-module coupling, judgement call.

## Spec (issue #79 + agent brief, comment #2)

**Satisfied (spot-checked against normative schema 1)**
- Envelope keys, enums, explicit nulls, coverage object with exactly 4 sources, errors `{source,code,message}` ≤32 and deduped: `factory/feedback.py:94-105,110-118`.
- `source_revision` over exactly the 14 listed fields: `:27-29,239`. `observation_id` over native IDs, final head, sorted pairs, coverage status/truncated/reason, excluding `observed_at`: `:438-440`.
- Bounds `100/2/20000/32/30s` as module constants: `:18-22`; page/item/body/timeout gaps set `truncated` and machine codes.
- Head race: initial+final reads, both SHAs in reason, relevance `unknown`, final head used for `pr.head_sha`: `:376-380,426-433`.
- `thread_outdated=true` never current; missing source SHA never filled from head: `:428-433,196-198`.
- Factory review requires accepted lifecycle `result` (rc 0, parsed, head==actual_head, enter before, exit outcome matching) — consistent with the producer at `factory/dispatch.py:468-492`; `VERDICT` prose ignored: `:406-420`, test `tests/test_feedback.py:85-100`.
- Ownership = same-repo closing-issue link (Factory PR bodies carry `Closes #n`, `factory/dispatch.py:1319`) + `claimed` event (`factory/dispatch.py:1297`): `:382-398`.
- Runtime-JSON path untouched; returns before `configure()`/`snapshot()`: `factory/dashboard.py:1228-1254`.
- Non-PR tickets: feedback attached only when `n in raw_prs`: `factory/dashboard.py:884`.
- Legacy fields preserved; test asserts `gate_text`/`review_decision`/`checks`/`comments`/`verdicts`: `tests/test_factory.py:2469-2474`.
- HTML renders body via `pre()` → text node (`factory/dashboard.html:938,1077`), links via `safeSourceURL` (`:1228`), `title`/`dataset` via setAttribute — no HTML interpolation.
- README/CHANGELOG document schema location, bounds, unknown/partial semantics, read-only authority.

**Changed signatures/flags/defaults — consumer check**
- `github(*, endpoint, query, variables, timeout)` (`factory/dashboard.py:196-222`): only non-diff caller is `github()` at `:815` (bare call → identical legacy query). Test patches at `tests/test_factory.py:598` and `tests/test_lifecycle_consumers.py:129` (`side_effect=RuntimeError`) still hold; snapshot catches `RuntimeError` at `:828`. New `errors` → `RuntimeError` is redundant with `gh`'s nonzero exit on GraphQL errors; no behavior change in practice.
- `GRAPHQL` adds `repository.id`, `issues.id`, `pullRequests.id/headRefOid` (`:124-151`): `pr_record`/`build_ticket` pick fields by name; holds.
- `sources_for` label `"Review verdict"` → `"Legacy review verdict · provenance unknown"` (`factory/briefing.py:209`): dashboard `openFM` matches regex `verdict|review` (`factory/dashboard.html:1073`), still matches. `"Evidence coverage · truncated"` → conditional suffix (`:402-407`): `factory/evidence.py:584` uses `startswith("Evidence coverage")`, holds.
- `tickets[].pr` gains `feedback`: `briefing` projects `pull_request` by explicit keys (`:231`); holds.

**Deviations / gaps (optional unless you confirm otherwise)**
1. **Open-PR-only detail collection** (`collect_details=raw.get("state") == "OPEN"`, `factory/dashboard.py:893`; `factory/feedback.py:129-131`). Not in the issue text or the agent brief; the escalation comment (#7) does not show an approved scope change (packet is a local file). Structurally compliant — closed/merged PRs still get a schema-1 object with explicit `unavailable`/`not_collected` — and documented in README/CHANGELOG, and defensible under "Preserve full-snapshot cadence". Confirm it was approved in the escalation packet; otherwise it is a narrowing of "project the identical feedback object at `tickets[].pr.feedback`".
2. `ownership_unverified` marks **`coverage.pr` partial** (`factory/feedback.py:402-403`) although the PR source read was complete. Coverage status is defined per source read; ownership is already explicit in `owner.relation`. Conservative, not harmful, but mislabels read completeness and makes every non-Factory `agent/*` PR show `pr: partial`.
3. Exit gate 4 asks for a regression boundary that `--runtime-json` performs no feedback/GitHub read; diff adds no such test. Code path is provably unchanged (`factory/dashboard.py:1228-1254`); gate PASS covers existing runtime tests.
4. `factory_review` `evidence_id` is namespaced under provider `github:` (`factory/feedback.py:212`) though its source is the Factory journal; `kind` disambiguates, no collision. Cosmetic vs. "namespaced by provider".

## Required fixes
None. No acceptance criterion or documented rule is violated with cited evidence; no concrete correctness/security defect found.

## Optional suggestions
- Pass the already-held `rows` from `lifecycle.journal_snapshot` (`factory/dashboard.py:837`) into `feedback.collect` instead of re-reading `events.jsonl` (`:850-873`); removes a duplicate read and the spurious `provenance_partial` race.
- Drop the `pr` coverage downgrade at `factory/feedback.py:402-403`; keep the error entry if you want it surfaced.
- Export a public `lifecycle.is_lifecycle_row` (or similar) instead of `_lifecycle` at `factory/feedback.py:406`.
- Add the runtime-JSON no-feedback regression if exit gate 4 is to be literally satisfied.

Summary — Standards: 4 judgement-call smells, no rule breaches. Spec: 4 non-blocking deviations; worst is the open-PR-only narrowing pending confirmation of approval.

VERDICT: APPROVE

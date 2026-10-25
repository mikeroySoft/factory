Inputs note: `./review.diff` and `./snapshot` do not exist in this sandbox; the clone itself is the gated revision (`HEAD` = `e20ebcd`, `origin/main` = issue base `8fba9c5`). Reviewed `git diff origin/main..HEAD` (9 files, +1476/−37). Issue #79 read via GitHub API (two comments: triage brief; attempt‑4 escalation). No approved scope-change comment exists.

## Standards

Repo has no AGENTS.md/CONTRIBUTING.md; `docs/` holds plans only; CI and `.factory.toml` run `python -m unittest discover -s tests` only. `pyproject.toml:36-37` sets `ruff line-length = 100` but nothing enforces it and existing modules already exceed it (dashboard.py 27 lines, briefing.py 60). No hard-standard violations.

Optional (judgement calls):
- `factory/feedback.py` is single-quoted throughout (e.g. `:604-607`, `:664-670`) while every sibling module uses double quotes. Not enforced; cosmetic.
- `factory/feedback.py:406` imports and calls the private `lifecycle._lifecycle`. Reuse is correct per the brief ("reuse accepted bounded lifecycle/event readers"); exposing it as a public name would be the tidy fix, not required.

## Spec

Schema‑1 envelope, item fields, enums, explicit nulls, `source_revision` field set (`feedback.py:27-29`, `:244`), `observation_id` composition (`:438-440`), sorted items/errors (`:436-437`), fixed bounds (`:18-22`), initial/final head reads with race → `unknown` + both SHAs in reason (`:376-380`), dedup of equivalent polls and identity-conflict handling (`:245-257`), sanitized fixed error messages (`:142-146`), VERDICT prose never promoted (factory reviews only from validated `result` events with matching `head`/`actual_head`, exit outcome, `:406-420`), REST/GraphQL transport reuse via the extended `dashboard.github` (`dashboard.py:196-224`), full‑snapshot‑only wiring (`dashboard.py:884-896`; runtime-json branch at `dashboard.py:1228` untouched; existing `tests/test_runtime.py` command audit still covers it), briefing appends after human constraints with omission/shortening notices (`briefing.py:286-376`), DOM via existing `h`/`pre`/`safeSourceURL` text-node helpers (`dashboard.html:993-1085`, `:1228`). Exit gates 1–4 have matching tests (`tests/test_feedback.py`, `tests/test_factory.py:2399-2538`, `tests/test_briefing.py:17-184`). Legacy `checks/comments/verdicts/review_decision/gate_text` preserved (`tests/test_factory.py:2471-2475`).

Deviation not in the issue or its comments: detail reads only for `OPEN` PRs (`dashboard.py:895`, `feedback.py:129-131`). Closed/merged PRs get the schema‑1 envelope with every source `unavailable` + `not_collected` and ownership `unverified`. The issue never requires detail for historical PRs and does require "preserve full-snapshot cadence"; the README/CHANGELOG/UI name the behaviour explicitly as distinct from empty/unsupported (`README.md:39-46`, `dashboard.html:1071-1073`). Not a defect; flagged as unapproved scope narrowing for the record.

Optional:
- `feedback.py:402-403` records `ownership_unverified` as a `pr` coverage gap, so any PR without a verified Factory link reports `coverage.pr.status = partial` even when the PR read itself completed. The schema defines coverage as read completeness; folding ownership into it means #15 cannot distinguish "PR read incomplete" from "ownership unverified" without parsing the reason string. Consider leaving coverage `complete` and relying on `owner.relation`.
- Ownership relies solely on `closingIssuesReferences` (`feedback.py:33-34`, `:387-396`). GitHub populates those links only for PRs whose base is the repository default branch `[INFERENCE]`; a factory configured with a non-default `main` (the issue itself cites PRs targeting `stable`) would never reach `factory_issue`, so `factory_review` items would never be emitted. Conservative and spec-compliant ("missing links remain explicit"), but worth confirming on the real host during exit-gate‑5 smoke.
- `dashboard.html:1093` drops the PASS/FAIL colouring from legacy verdict chips. Within the allowed "existing tabReview presentation" scope, but it is a visible downgrade unrelated to the acceptance criteria.

## Plan drift

Plan status "not recorded"; no planned file list exists to diff against. All nine changed files are inside the issue's `Touches` list; `tests/test_feedback.py` is permitted ("only if smaller and clearer") and is a dedicated 334‑line contract file rather than an append to the 129 KB `test_factory.py`.

Proof not evidenced in the gate report (gate only lists conflict-markers/test/leak-scan): exit gate 5 (disposable repo `--json` inspection, live server + browser check of Review drawer/Inbox incl. literal `<img onerror…>`, `sources_for` bundle without `respond`, no-mutation record, cleanup) and exit gate 7 handoff packet with a sanitized full-snapshot example. These live in the worker handoff/PR body, which was not supplied; the only handoff text visible is the truncated attempt‑4 JSON in the issue comment. Absence here is unverified, not established, and exposes no defect in the diff.

## Required fixes

None.

VERDICT: APPROVE

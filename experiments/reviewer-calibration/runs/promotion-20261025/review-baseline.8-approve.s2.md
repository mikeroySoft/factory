## Standards

No `AGENTS.md`/`CONTRIBUTING.md` in the repo; conventions come from README and the issue template. Legacy ticket events in `.factory/events.jsonl` are open-ended (`README.md:399-400`), so the new `review-readiness` row via `record()` fits the documented contract. Events written under `ticket_lock` and skipped on `dry_run` (`factory/dispatch.py:232-233`, `:268-271`) match the existing lane pattern.

- **Optional** — `README.md:349` still states the lane "does not … monitor CI"; `factory/dispatch.py:276-279` now runs `gh pr checks` every pass, and the new `review-readiness` event is undocumented next to `review-intake` (`README.md:337-339`). No written rule mandates doc sync (prior #7 commit touched no docs), so non-blocking; one sentence fixes the contradiction.

## Spec (issue #8)

Exit-gate criteria all covered by the diff:

- Missing/unparsable/pending/failed/cancelled not ready: `factory/dispatch.py:292-296` rejects empty, non-list, malformed rows; `:305-310` maps `fail`/`cancel` → `ci_failed`, anything else without confirmed head + rc 0 + all `pass` → `ci_pending`. Tests `tests/test_factory.py:1391-1404`.
- Approve + passing required checks on same head → `ready`: `:307-310`; test `:1404`.
- New head clears readiness: review matched on `head` from fresh `gh pr view` (`:287-289`, `:297-299`); tests `:1411-1418` (push mid-query → `review_pending`, head-2 unmatched → `review_pending`).
- No `gh pr merge`: no merge call in diff; asserted `tests/test_factory.py:1426`.
- Distinct states `ci_pending`/`ci_failed`/`changes_requested`/`ready` (+`review_pending`) at `:300-310`.
- `review_intake_pass` widening at `factory/dispatch.py:220` (`APPROVE` keeps the PR tracked after GitHub consumes the review request) is required so approved PRs keep receiving readiness updates; justified by the "ready" criterion.

Fail-closed path check: `json.loads` failure on `checks.stdout` short-circuits the `fresh` parse (`:284-291`) → `confirmed=False`, `rows=[]` → can never reach `ready`. Correct direction.

- **Optional** — `factory/dispatch.py:305-306`: `ci_failed` can be recorded without `confirmed`, i.e. checks for a newer head attributed to the stale `head` when `gh pr view` fails. Not-ready direction, so no safety impact; gate `confirmed` on it too if event consumers later care about attribution.
- **Optional** — `factory/dispatch.py:232-233` appends a `review-readiness` row for every tracked PR on every pass, unconditionally. Journal grows linearly with pass count × tracked PRs. Record only when `state`/`head` differs from the last readiness row if this becomes a problem.

## Plan drift

No plan recorded. Files changed (`factory/dispatch.py`, `tests/test_factory.py`) are exactly the issue's "Touches". Gate `test` check runs `python -m unittest discover -s tests` — equivalent to the issue's `python -m unittest` proof; PASS.

## Required fixes

None.

VERDICT: APPROVE

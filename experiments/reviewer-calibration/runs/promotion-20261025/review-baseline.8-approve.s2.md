## Standards

No `AGENTS.md`/`CONTRIBUTING.md` in the snapshot; documented sources are `README.md`, `CHANGELOG.md`, `pyproject.toml` (`[tool.ruff] line-length = 100`, not run in CI; `tests/test_factory.py` already has 136 lines >100, so not enforced).

**Required:** none.

**Optional**
- `review.diff:23-24` (`factory/dispatch.py:232-233`): `review_readiness` runs unconditionally for every tracked PR on every non-dry pass, appending a `review-readiness` row (with the full `checks` list) plus two `gh` subprocesses per PR even when nothing changed. `record()` docstring (`factory/dispatch.py:404`) describes the journal as "every stage transition"; a 10-min timer × N open approved PRs adds ~144 rows/PR/day that bounded tail readers (`runtime_events.EVENT_LIMIT = 512`, `BYTE_LIMIT = 1 MiB`) will evict older execution evidence behind. Cheapest fix: skip `record` when the last `review-readiness` row for `(pr, head)` has the same `state`.
- `README.md:349` still states the lane does not "monitor CI"; `review.diff:35-73` now does. `CHANGELOG.md` `## Unreleased` has no entry. Neither file is in the issue's Touches list, so non-blocking.

Smell baseline: none worth flagging. `review_readiness` is a single function with one caller pattern; no speculative generality.

## Spec (issue #8 + triage brief)

| Criterion | Evidence | Status |
|---|---|---|
| Query check state for opt-in lane PRs | `review.diff:37-40` `gh pr checks --required --json name,bucket` | met |
| Distinct states ci_pending / ci_failed / changes_requested / ready | `review.diff:61-71` | met (plus `review_pending`) |
| Ready = approval for current head **and** all required checks pass | `review.diff:58-60` review bound to `head`; `:68-71` requires `confirmed and valid and returncode == 0 and all pass` | met |
| Later head invalidates readiness | `review.diff:48-50` fresh `headRefOid` replaces `head`; review lookup then misses → `review_pending`; test `:134-141` | met |
| Missing / unparsable / pending / failed / cancelled not ready | `review.diff:44-57` fail-closed `valid`; tests `:113-127` cover `[]`, `not json`, `null`, `[{}]`, bad bucket types, pending, fail, cancel, skipping, pass-with-nonzero-exit | met |
| Events via existing `record()` | `review.diff:72-73` | met |
| No `gh pr merge` for external PRs | `review.diff:109` mock fails on any unexpected command; `:148` explicit assert | met |
| Dry run writes nothing | `review.diff:23, 145-147` | met |
| `python -m unittest` passes | gate `test: PASS` | met |

Eligibility change `review.diff:15` (keep tracking `APPROVE`d PRs after the label/request is consumed) is necessary for readiness to be recorded post-approval; not scope creep. Note the pre-existing `review.diff:25-27` short-circuit means an approved PR's new head is never re-reviewed, so its readiness stays `review_pending` — that is #7's contract ("transitions end on approval"), not #8.

Race window: `gh pr checks` runs before `gh pr view`; a push between the two yields `confirmed = False` → not ready; a push after both yields readiness bound to the old head, which the next pass clears. Fail-closed in both orders.

## Plan drift

Plan not recorded. Files changed: `factory/dispatch.py`, `tests/test_factory.py` — both named in the issue's Touches. Planned proof (`python -m unittest`) present in gate report. No drift beyond the undocumented README/CHANGELOG staleness above.

VERDICT: APPROVE

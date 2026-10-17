## Standards

Repo has no `AGENTS.md`/`CONTRIBUTING.md`; documented sources are `README.md` and `pyproject.toml` (`[tool.ruff] line-length = 100`, not a gate check in `.factory.toml`).

**Required:** none.

**Optional**
- `README.md:349` still states the lane does "not … monitor CI" and `README.md:337-338` documents only `review-intake` events. `factory/dispatch.py:276-282` now runs `gh pr checks`/`gh pr view` per tracked PR and `factory/dispatch.py:311-312` emits a new `review-readiness` event (`state`, `review_head`, `checks`). Sibling commits for #5/#6 updated this README section; this one leaves it contradicting the code. Issue "Touches" does not list README, so non-blocking.
- `tests/test_factory.py:1389,1416,1422` exceed ruff's 100-column config. Not enforced (133 pre-existing violations in the file); mention only.
- `factory/dispatch.py:233` records one `review-readiness` row per tracked PR per dispatcher pass, unconditionally, with `checks` rows embedded — and `factory/dispatch.py:221` re-reads the whole journal once per PR per pass. Append-only journal (`README.md:399`) grows ~N PRs × 144 rows/day with no state change. Cheap mitigation: skip `record` when `(head, state)` equals the last `review-readiness` row for `pr`; worth a `# ponytail:` ceiling comment if left as is.
- `factory/dispatch.py:284-291`: a `ValueError` on `checks.stdout` (e.g. `gh pr checks --required` with no required checks prints nothing and exits 1) skips the `fresh` parse, so head confirmation is never attempted and the row is recorded against the possibly-stale list head. Still fail-closed (`confirmed=False` ⇒ never `ready`); only the recorded `head` can lag one pass. Parsing the two outputs in separate `try` blocks removes the coupling.
- `factory/dispatch.py:275` docstring says "for a confirmed head"; the function records unconfirmed heads too (as non-ready). Minor naming accuracy.

## Spec (issue #8 + agent brief)

| Criterion | Evidence | Status |
|---|---|---|
| Query check state for opt-in lane PRs | `dispatch.py:276-279` (`gh pr checks --required --json name,bucket`), called at `dispatch.py:233` and `:271` | ✓ |
| Fail-closed readiness from review + CI | `dispatch.py:292-310`: `ready` requires `confirmed and valid and returncode == 0 and all pass`; any parse/shape failure ⇒ `valid=False` | ✓ |
| Distinct states: CI pending / CI failed / changes requested / ready | `dispatch.py:300-310` (`review_pending`, `changes_requested`, `ci_pending`, `ci_failed`, `ready`) | ✓ |
| Ready only when review approves the **current** head | review lookup keyed on `head` after refresh (`dispatch.py:287-289, 297-299`) | ✓ |
| Later head invalidates readiness | `dispatch.py:287-289` swaps to fresh head ⇒ no matching review ⇒ `review_pending`; tested `tests/test_factory.py:1413-1421` | ✓ |
| Never `gh pr merge` for external PRs | no merge call in diff; asserted `tests/test_factory.py:1428` | ✓ |
| Events via existing `record()` | `dispatch.py:311-312` | ✓ |
| Tests: missing / unparsable / pending / failed / cancelled not ready | `tests/test_factory.py:1392-1405` (`[]`, `null`, `not json`, `[{}]`, list-typed bucket, unknown bucket, pending, fail, cancel, skipping) | ✓ |
| Tests: approval + passing required checks same head ⇒ ready | `tests/test_factory.py:1406` | ✓ |
| Tests: stale approval | `tests/test_factory.py:1413-1421` | ✓ |
| Out of scope respected (no branch pushes, no merges, no dashboard) | diff touches only `dispatch.py` and tests | ✓ |

Supporting change `dispatch.py:218-223` (track `APPROVE`-history PRs after GitHub consumes the review request) is needed for readiness of approved PRs and does not widen review behavior: `dispatch.py:234-236` still skips intake after any APPROVE, so `test_external_review_head_transitions_end_on_approval` semantics are intact.

Scope creep: none. Missing/partial: none.

**Summary:** Standards — 0 required, 5 optional (worst: stale README claim at `README.md:349`). Spec — 0 findings.

VERDICT: APPROVE

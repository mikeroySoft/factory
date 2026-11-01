The diff meets every exit-gate criterion in issue #8, and I found nothing that requires a revision.

**Standards.** The snapshot has no `AGENTS.md` or `CONTRIBUTING.md`, and nothing in `docs/` sets a rule this diff breaks. The new `review_readiness` helper is called from two places (`factory/dispatch.py:233` and `:271`) and its docstring says it records advisory readiness only, never merge authorization. It is needed and adds no extra abstraction. Events go through the existing `record()` function, as the brief requires. The only doc problem is a stale README (nit below).

**Spec.** Each exit-gate criterion maps to code and a test:
- **Missing, unparsable, pending, failed or cancelled checks are not ready:**
  - Rows must parse as a non-empty list with known `bucket` values (`dispatch.py:292-296`). Anything else gives `ci_pending`.
  - `fail` or `cancel` gives `ci_failed` (`:305-306`).
  - `ready` also needs the fresh head to be confirmed, the checks command to exit 0, and every row to pass (`:307-310`).
  - Tests: `tests/test_factory.py:1391-1406`.
- **Approval plus passing required checks for the same head is ready:** the review is looked up by head (`:297-299`); tested at `test_factory.py:1405`.
- **A new head clears readiness:** if the head changes between the PR list and the re-fetch, `confirmed` is false and the review is looked up at the new head, giving `review_pending`. Tested at `test_factory.py:1412-1419`.
- **No `gh pr merge` for an external PR:** asserted at `test_factory.py:1426`; no merge path was added.
- **Approved PRs stay tracked:** after GitHub consumes the review request, a PR whose last result is `APPROVE` is still picked up (`:220`). It then records readiness and stops at the existing approval `continue` (`:234`), so no new reviews are triggered.

**Plan drift.** The plan was not recorded. Both changed files (`factory/dispatch.py`, `tests/test_factory.py`) are named in the issue's Touches list, and the planned proof (`python -m unittest`) matches the gate's `test: PASS`. There is no drift.

```json
{"verdict":"APPROVE","findings":[{"severity":"nit","pass":"bug","path":"factory/dispatch.py","line":233,"summary":"A review-readiness row is appended on every dispatcher pass for every tracked open PR, including PRs approved or escalated long ago, even when (head, state) has not changed. On a pass that admits a new revision it is written twice (here and at :271). The record() docstring says rows are for stage transitions, so events.jsonl grows by at least one row per PR per pass. Skipping the write when the last readiness row for this PR has the same head and state would stop this."},{"severity":"nit","pass":"bug","path":"factory/dispatch.py","line":285,"summary":"Both JSON parses share one try block. If `gh pr checks` output does not parse (for example, no required checks, exit 1, empty stdout), the fresh headRefOid is never applied, so the event records the head from the PR list, which may be stale. This still fails closed (never `ready`) and is corrected on the next pass."},{"severity":"nit","pass":"spec","path":"factory/dispatch.py","line":294,"summary":"A required check in the `skipping` bucket is allowed but can never pass (:308), so the PR stays `ci_pending` forever. GitHub treats skipped required checks as satisfied, so `ci_pending` is the wrong label for a final state. Not ready is still acceptable under 'all reported required checks pass'."},{"severity":"nit","pass":"spec","path":"factory/dispatch.py","line":274,"summary":"The new readiness tracking makes README.md:349 ('This lane does not ... monitor CI') stale. The README's intake section (README.md:337-338) does not document the new `review-readiness` event, and CHANGELOG.md Unreleased has no entry like those for other issues (e.g. #79). No documented rule requires this and the issue's Touches list leaves out docs, so this is not blocking."}]}
```
VERDICT: APPROVE

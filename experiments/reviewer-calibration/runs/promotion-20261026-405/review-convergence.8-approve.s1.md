**APPROVE.** I found no important findings. The diff covers every exit-gate criterion in issue #8 and adds nothing beyond the brief.

**Spec (issue #8 and its agent brief):**
- **Distinct states:** `review_readiness` records `review_pending`, `changes_requested`, `ci_pending`, `ci_failed` and `ready` through the existing `record()` call (`factory/dispatch.py:300-312`).
- **Ready requires all of:** an `APPROVE` result for the same head SHA, a confirmed fresh head, a non-empty list of valid required-check rows, every bucket `pass`, and `gh pr checks` exiting 0 (`factory/dispatch.py:297-310`).
- **Not ready:** missing (`[]`), unparsable (`not json`, `null`, `[{}]`), invalid bucket, pending, failed and cancelled checks all stay not ready. The tests cover each of these (`tests/test_factory.py:1391-1405`).
- **New head clears readiness:** if the head changes during the query, or the listed head moves, the PR returns to `review_pending` (`tests/test_factory.py:1412-1419`).
- **No merge:** the new code never calls `gh pr merge`, and the test asserts this (`tests/test_factory.py:1426`). A dry run writes nothing (`tests/test_factory.py:1423-1425`).

**Standards:** the repo has no AGENTS.md, CONTRIBUTING.md or coding-standards doc. The README and CHANGELOG are the only documented conventions, and the README is now out of date.

**Plan drift:** no plan was recorded. The changed files match the issue's Touches list exactly (`factory/dispatch.py`, `tests/test_factory.py`, events written through `record()`). No README or CHANGELOG update was made; neither is in Touches. The gate report has no proof that it is missing.

**Abstraction:** `review_readiness` is the one new function. It is needed because the issue requires a check-state query and readiness derivation, and it is called from both intake paths.

I did not run any builds or tests, as instructed.

```json
{"verdict":"APPROVE","findings":[{"id":"F1","severity":"nit","pass":"spec","class":"stale-lane-docs","path":"factory/dispatch.py","line":275,"summary":"The lane now queries required CI checks and records review-readiness events, but README.md:349 still says this lane does not monitor CI. The README review-lane paragraph (README.md:335-349) does not document the review-readiness event, and CHANGELOG.md Unreleased has no #8 entry. README is not in the issue's Touches list, so this is not blocking.","instances":[{"path":"factory/dispatch.py","line":275,"summary":"New CI readiness tracking contradicts the README statement 'This lane does not ... monitor CI'."},{"path":"factory/dispatch.py","line":311,"summary":"The new review-readiness event (pr, head, state, review_head, checks) is undocumented next to the review-intake event docs in README.md:337-338."}]},{"id":"F2","severity":"nit","pass":"bug","class":"unconditional-readiness-rows","path":"factory/dispatch.py","line":233,"summary":"Every non-dry pass makes two gh calls and appends a review-readiness row for every tracked PR, even when the state has not changed. Tracked PRs include ones that are approved, have requested changes, or are escalated, and they stay tracked for as long as they are open. On the default 10-minute timer, an idle PR produces about 144 rows per day in .factory/events.jsonl, and briefing and dashboard read all of them. Writing a row only when (head, state, checks) differs from the last review-readiness row for that PR would avoid this.","instances":[{"path":"factory/dispatch.py","line":233,"summary":"Readiness is recorded every pass before the APPROVE, escalate and already-intaken early exits."},{"path":"factory/dispatch.py","line":271,"summary":"A newly admitted head is recorded twice in one pass: review_pending at line 233, then again after the review."}]}]}
```
VERDICT: APPROVE

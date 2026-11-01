No blocking defects; the diff can be approved. It satisfies the exit gate for issue #8, and the gate report already shows the tests passing.

**Spec check (issue #8):**
- **Not ready on bad checks:** missing, unparsable, pending, failed and cancelled checks never produce `ready`. `valid` requires a non-empty list with known buckets (`factory/dispatch.py:292-296`). `ready` also requires a confirmed head and a zero exit code (`:307-310`). The test cases at `tests/test_factory.py:1393-1406` cover these.
- **Ready only on the current head:** an approval counts only when its head matches the PR's current head (`factory/dispatch.py:297-299`). The head is checked again after the checks query (`:280-289`).
- **New head clears readiness:** a push during the query, or a new head, gives `review_pending` (`tests/test_factory.py:1412-1419`).
- **No merge:** the diff never issues `gh pr merge` (`tests/test_factory.py:1426`).

**Scope:** both changed files (`factory/dispatch.py` and `tests/test_factory.py`) are named in the issue's Touches list. Events go through the existing `record()`. The worker plan was not recorded, so there is no plan drift to report and no planned proof missing from the gate report.

**Standards:** the snapshot has no `AGENTS.md` or `CONTRIBUTING.md`. `README.md` and `docs/` contain no rule that this diff breaks.

**New function:** the diff adds one helper, `review_readiness`. It is justified: the issue's scope asks for a check-state query and readiness derivation, and it is called from two places (`:233`, `:271`).

The two nits are both non-blocking.

```json
{"verdict":"APPROVE","findings":[{"severity":"nit","pass":"bug","path":"factory/dispatch.py","line":233,"summary":"review_readiness runs on every dispatch pass for every tracked PR, and the widened tracking at :220 keeps approved PRs in that set until they close. :311 then appends a review-readiness row every pass even when nothing changed. With the 10-minute timer (README.md:61) that is about 144 rows per day per open approved PR, which fills tail-limited journal readers such as briefing and runtime-json history. The README already uses change-only rows for scheduling observations (README.md:677). Recording only when the state or head changes would avoid this."},{"severity":"nit","pass":"spec","path":"factory/dispatch.py","line":274,"summary":"The review lane now monitors required CI and records review-readiness events, but README.md:349 still says 'This lane does not change contributor branches, monitor CI, or merge external PRs', and the README event contract (README.md:337-338) does not mention the new event. The documented contract is now out of date."}]}
```
VERDICT: APPROVE

**Verdict: REVISE.** One important finding blocks approval; the rest are nits.

**Important: full-snapshot cadence is not preserved.** `factory/dashboard.py:883-892` calls `feedback.collect` once per selected `agent/<n>` PR, one PR after another. That includes merged and closed PRs anywhere in the existing `pullRequests(first:100)` window, and it runs on every full `snapshot()`. Each PR needs at least six `gh` calls: two head GraphQL reads, reviews, threads, check runs and statuses. Each PR has its own 30-second deadline, but nothing bounds the total across PRs.

- **Latency:** `cached_snapshot` holds `_cache_lock` while `snapshot()` runs, and the dashboard polls every 30 seconds against a 15-second TTL. Snapshot time therefore grows with the number of PRs, and `/api/snapshot` and FM requests are blocked behind it.
- **Rate limits:** with a visible dashboard tab, about 120 snapshots per hour × 3 REST calls per PR is 360 calls per PR per hour. With about 14 or more Factory PRs, that exceeds GitHub's 5,000-request hourly REST limit; GraphQL points grow the same way. The dispatcher uses the same `gh` credentials, so its reads would start failing too.
- **Rule broken:** issue #79 says "Preserve full-snapshot cadence and existing ticket/PR fields."

**Nits:**
- `factory/dashboard.py:876-878`: the producer revision comes from `git rev-parse HEAD` in the installed package directory. If Factory is installed in a gitignored `.venv` inside another repository, it reports that repository's HEAD. Issue #79 requires "a verifiable source/build revision when available, otherwise null."
- `factory/dashboard.py:216`: the shared `github()` transport now replaces `gh` stderr with "GitHub read failed". The existing snapshot `errors` lose their auth and rate-limit diagnostics, even though `feedback.collect` already sanitizes its own errors.
- `factory/dashboard.py:150`: `headRefOid` was added to the snapshot GraphQL query but is never used.
- `factory/feedback.py:237`: when an item is dropped for a contradictory identity, it still counts toward `ITEM_LIMIT`. That can report a spurious `item_limit` truncation.

**Standards:** the snapshot has no `AGENTS.md` or `CONTRIBUTING.md`, and `docs/` holds only plans. The README and CHANGELOG updates follow the existing patterns, and the `h()` helper renders source bodies as text, not HTML.

**Plan drift:**
- No worker plan was recorded. Every changed file is named in the issue's Touches list. `tests/test_feedback.py` is the optional separate test file the issue allows.
- The gate report lacks the planned proof from exit gates 5 and 7: no disposable-repo run of `python -m factory dashboard --json`, no live-server browser check of the Review drawer and Inbox, no `sources_for` smoke run, and no sanitized handoff example. That doesn't block on its own, but it means the cadence and rate-limit cost was never observed.

```json
{"verdict":"REVISE","findings":[{"severity":"important","pass":"bug","path":"factory/dashboard.py","line":885,"summary":"feedback.collect runs serially for every selected agent/<n> PR (including merged/closed, up to the 100-PR window) on every full snapshot: >=6 gh calls per PR, a separate 30s deadline per PR, no aggregate bound, while cached_snapshot holds _cache_lock and the UI polls every 30s against SNAPSHOT_TTL=15. Snapshot latency grows linearly with PR count, and about 14 or more PRs exhaust GitHub's 5000/h REST limit shared with the dispatcher. Violates issue #79 'Preserve full-snapshot cadence and existing ticket/PR fields'."},{"severity":"nit","pass":"spec","path":"factory/dashboard.py","line":876,"summary":"Producer revision is `git rev-parse HEAD` from the installed package dir. If the package sits in a gitignored path (e.g. .venv) inside another repo, it reports that repo's HEAD; issue #79 requires a verifiable source revision or null."},{"severity":"nit","pass":"bug","path":"factory/dashboard.py","line":216,"summary":"Shared github() transport now replaces gh stderr with 'GitHub read failed', removing auth and rate-limit diagnostics from existing snapshot errors. The feedback collector already maps exceptions to fixed messages."},{"severity":"nit","pass":"bug","path":"factory/dashboard.py","line":150,"summary":"headRefOid was added to the snapshot GraphQL query but is never consumed."},{"severity":"nit","pass":"bug","path":"factory/feedback.py","line":237,"summary":"Dropping a conflicting duplicate identity does not decrement counts[source], so the removed item still consumes ITEM_LIMIT and can trigger a spurious item_limit truncation."}]}
```
VERDICT: REVISE

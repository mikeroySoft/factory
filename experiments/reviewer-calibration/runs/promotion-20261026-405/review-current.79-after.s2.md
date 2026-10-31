I found one important defect, so this needs revision. I did not run builds or tests, as instructed. Issue #79 and its comments were read from the public GitHub page. The snapshot has no AGENTS.md or CONTRIBUTING.md, so the standards check used the issue's rules plus the existing code in `factory/`.

**The inline-thread query fails against real GitHub.** `factory/feedback.py:45` asks for `diffSide` on each review comment. GitHub only has that field on the review thread, not on the comment; the published GraphQL schema (octokit/graphql-schema) confirms this. GitHub rejects the whole query, `gh api graphql` exits nonzero, and `request()` turns that into `source_unavailable`. On a real host, `coverage.threads` is therefore always `unavailable` and no `review_comment` items are ever produced. That breaks the issue's requirement to "Query real reviews and real review threads/comments … with native IDs, links, commit identity, and resolved/outdated state", plus exit gates 1 and 3.

The test suite passes because `tests/test_feedback.py` uses a fake `Provider`. The exit-gate 5 smoke also uses provider stand-ins, so it would miss this too. Fix: request `diffSide` on the thread and read `thread.get('diffSide')` at `factory/feedback.py:319`.

**Spec checks that pass:**
- The schema-1 envelope has all the required fields.
- `source_revision` and `observation_id` are computed from the fields the issue lists.
- Bounds, head-race handling and one-source-failure handling match the issue.
- Factory reviews need a valid lifecycle record of the review run, so `VERDICT` text alone is not trusted.
- Feedback is not collected on the `--runtime-json` path.
- Non-PR tickets get no feedback object.
- Briefing adds feedback after the earlier human constraints and reports what it leaves out.
- Source text is rendered only through `h()`/`pre()`, which insert it as text, not HTML.

**Plan drift:** no plan was recorded. Every changed file is named in the issue's Touches list; `tests/test_feedback.py` is allowed there. The proof from exit gates 5 and 7 (real CLI and browser smoke, sanitized example) is missing from the gate report. That gap does not hide anything beyond the defect above.

```json
{"verdict":"REVISE","findings":[{"severity":"important","pass":"bug","path":"factory/feedback.py","line":45,"summary":"THREAD_QUERY selects `diffSide` on PullRequestReviewComment, but GitHub's GraphQL schema defines diffSide only on PullRequestReviewThread. GitHub rejects the whole query, gh exits nonzero, and request() records source_unavailable. In production `coverage.threads` is always unavailable and no review_comment items are produced. This violates issue #79 ('Query real reviews and real review threads/comments with native IDs, links, commit identity, and resolved/outdated state') and exit gate 1 (an unresolved inline thread/comment must survive). Tests miss it because they use a fake Provider. Fix: request diffSide on the reviewThreads node and read thread.get('diffSide') at feedback.py:319."},{"severity":"nit","pass":"bug","path":"factory/feedback.py","line":251,"summary":"When a source-identity conflict removes the earlier item, counts[source] (incremented at line 257) is not decremented, so a removed item still uses one of the 100 retained-item slots."},{"severity":"nit","pass":"spec","path":"factory/dashboard.py","line":888,"summary":"feedback.collect runs one PR after another inside snapshot(), which runs under cached_snapshot's lock with a 15 s TTL. Each open PR adds about 7 gh calls and up to 30 s, so full-snapshot latency grows with the number of open PRs. The issue asks to preserve full-snapshot cadence."},{"severity":"nit","pass":"spec","path":"factory/dashboard.html","line":1260,"summary":"Task facts now call the deterministic local gate 'Legacy local gate failures/context'. The issue's legacy reframing covers verdict comments and rollups, not the exact-head gate, so this label understates authoritative gate evidence."},{"severity":"nit","pass":"spec","path":"factory/dashboard.html","line":1483,"summary":"Plan drift / missing proof: exit gates 5 and 7 require the real `python -m factory dashboard --json` and browser Review/Inbox smoke plus a sanitized schema-1 example. The gate report covers only conflict-markers, test and leak-scan, and no plan was recorded."}]}
```
VERDICT: REVISE

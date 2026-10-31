**Verdict: REVISE.** I found one Important bug: the review-thread GraphQL query is invalid against GitHub's schema, so production never collects any inline review-thread comments.

**F1: thread evidence is never collected.**
- `THREAD_QUERY` asks for `diffSide` on each comment (`factory/feedback.py`, `review.diff:662`). On GitHub that field exists only on the thread object (`PullRequestReviewThread`), not on the comment object (`PullRequestReviewComment`). I checked this against the published GitHub GraphQL schema (octokit/graphql-schema).
- GitHub checks the whole query before running it, so this field makes every threads request fail. `github()` raises (`review.diff:528-536`). `collect()` then records `source_unavailable` (`review.diff:761-762`).
- **Trigger:** every open PR in the full `factory dashboard --json` snapshot.
- **Impact:**
  - `coverage.threads` is permanently `unavailable`.
  - No `review_comment` items ever appear.
  - The issue's line "Query real reviews and real review threads/comments, with native IDs, links, commit identity, and resolved/outdated state" is not met.
  - Exit gate 1's "unresolved inline thread/comment" survives only in stand-ins, not on the real collector.
- **Why the passing tests miss it:** the test stand-in `Provider` returns `diffSide` on the comment (`review.diff:1422`), so tests never check the query against the real schema.
- **Fix:** select `diffSide` on the thread node next to `isResolved`/`isOutdated`, and set `side` from `thread.get('diffSide')` (`review.diff:936`).

**Things I checked that look correct:**
- The Factory-review provenance checks match the fields the reviewer's `result` event actually records (`dispatch.py:476-495`).
- The `claimed` event key matches `dispatch.py:1297`, and Factory PR bodies contain `Closes #n`.
- `h()` skips `open: false`, and source text renders through text nodes.
- The other GraphQL fields queried (reviews, PR, repository) all exist in the schema.

**Plan drift:** no plan was recorded, so I compared against the issue's Touches list.
- **Files:** every changed file is named there; `tests/test_feedback.py` is explicitly allowed.
- **Missing proof:** the gate report has only conflict-markers, test and leak-scan.
  - Exit gate 5 is absent: the disposable CLI `python -m factory dashboard --json` run, the live-browser check of the Review drawer and Inbox, and the `sources_for` bundle inspection.
  - Exit gate 7 is absent: the handoff with a real sanitized schema-1 example.
- **Why it matters:** this missing proof is how F1 got past review, though gate 5's provider stand-ins might not have caught it either.

```json
{"verdict":"REVISE","findings":[{"id":"F1","severity":"important","pass":"bug","class":"invalid-graphql-field","path":"factory/feedback.py","line":662,"summary":"THREAD_QUERY selects `diffSide` on PullRequestReviewComment, which has no such field (it exists only on PullRequestReviewThread). GitHub validates the whole query, so every threads read fails: in the full snapshot `coverage.threads` is always `unavailable`/`source_unavailable` and no `review_comment` items are ever emitted for any open PR. This violates issue #79's 'Query real reviews and real review threads/comments, with native IDs, links, commit identity, and resolved/outdated state' and exit gate 1 (unresolved inline thread/comment evidence). Select `diffSide` on the thread node and read the side from the thread.","instances":[{"path":"factory/feedback.py","line":662,"summary":"`diffSide` requested on review comment nodes in THREAD_QUERY"},{"path":"factory/feedback.py","line":936,"summary":"location.side read from comment `diffSide`, which the provider cannot return; should come from the thread"},{"path":"tests/test_feedback.py","line":1422,"summary":"Provider stand-in returns `diffSide` on the comment, so tests never check the query against the real schema"}]}]}
```
VERDICT: REVISE

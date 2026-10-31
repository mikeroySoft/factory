I found no Important findings, so the verdict is APPROVE. I only read the code; nothing was built or run, and the gate report is the build/test evidence.

**Standards:** The snapshot has no AGENTS.md or CONTRIBUTING.md. Under `docs/` I found plans, not contributor rules, and no rule that this diff breaks. CHANGELOG.md was not updated, but nothing documented requires it and the issue's file list doesn't name it, so I didn't raise it.

**Spec (issue #9 and the agent brief in its comment):**
- **Six states:** `review_queue` (`factory/dashboard.py:811-859`) puts each open, non-draft PR into one of the six states. It uses the same opt-in rules as `dispatch.review_intake_pass`: the `needs-review` label, a review request to the viewer, or an earlier APPROVE/REQUEST_CHANGES result.
- **SHA binding:** The state comes only from review and CI results recorded for the current head SHA (`factory/dashboard.py:834-837`). An approval on an older head stays "pending review", and the test covers that case (PR #1).
- **Rows and fields:** Each row shows the PR number, title, author, current head, last reviewed SHA, verdict, CI state and when CI was last checked. Each row links to GitHub (`factory/dashboard.html:1135-1143`).
- **No mutation controls:** Inbox entries for external reviews show a read-only row instead of the decision form (`factory/dashboard.html:1550-1551`).
- **Exit gate:** The test covers all six states, the PR links, the head SHA and last reviewed SHA, the CI state, and checks that a snapshot doesn't append to the events file (`tests/test_factory.py:2778-2840`).

**Plan drift:** No plan was recorded. The changed files are exactly the four the issue lists (`dashboard.py`, `dashboard.html`, `tests/test_factory.py`, `README.md`). The gate report doesn't include a manual `factory dashboard` run. The test's no-mutation check and the read-only GraphQL path cover that, so it doesn't hide a required defect.

```json
{"verdict":"APPROVE","findings":[{"severity":"nit","pass":"bug","path":"factory/dashboard.py","line":875,"summary":"The review queue is built from the existing `pullRequests(first:100, orderBy CREATED_AT DESC)` query, which includes closed and merged PRs. Once 100 or more newer PRs exist, an older open opted-in PR drops out of both the queue and the Inbox, while dispatch still tracks it via `gh pr list --state open --limit 1000`. The PRs most likely to be dropped are the long-stalled ones. Fix: query open PRs separately if repositories get that busy."},{"severity":"nit","pass":"bug","path":"factory/dashboard.html","line":1162,"summary":"The `pr.reason` part of the pending-review Inbox condition can never be true. `reason` comes only from an escalate event, which always sets the state to 'escalated'. `current.get(\"reason\")` (factory/dashboard.py:857) is always empty because dispatch's `review-result` events have no reason field (dispatch.py:363). This is dead logic, not a behaviour defect."}]}
```
VERDICT: APPROVE

I found no Important defects; the verdict is APPROVE. I read the diff and the snapshot only and did not run anything, as instructed.

**Spec (issue #9 and the agent brief in its comment):**
- **Opt-in rule:** `factory/dashboard.py:811-859` adds `review_queue`, which picks PRs the same way dispatch does (`dispatch.py:208-223`). A PR counts if it is open and not a draft, and it has the `needs-review` label, or a review request to the viewer, or an earlier APPROVE/REQUEST_CHANGES result.
- **State classification:** it matches the six states in the issue. A verdict only counts if it was given on the current head commit (SHA), and CI is read only from the `review-readiness` event for that same SHA. So an old approval cannot make a new head "ready" (`dashboard.py:834-849`). Escalation overrides every other state, which matches dispatch: once a PR is escalated, dispatch stops reviewing it (`dispatch.py:237`).
- **Fields and links:** each row shows the PR number, title, author, current head, last reviewed SHA, verdict, CI state and when CI was last observed, and links to GitHub (`dashboard.html:1134-1143`). Text goes in through `createTextNode`, so PR titles cannot inject markup.
- **Inbox:** stalled items appear there with links and no mutation controls (`dashboard.html:1160-1166`, `:1550-1551`).
- **Exit-gate test:** `tests/test_factory.py:2778-2840` covers all six states and checks PR links, head, author and title for every row. It checks the SHA-specific data (an old-head approval shows as `review_pending` with CI `unknown`). It also checks that a PR stays in the queue after its review request is consumed, and that a request to another reviewer, a closed PR and a draft are all left out.
- **README:** the review-lane description is updated. It also corrects the old claim that the lane does not monitor CI, which #8 made false.

**Standards:** the snapshot has no AGENTS.md or CONTRIBUTING.md, and docs/ has no rule this diff breaks. The repo has no rule requiring a CHANGELOG entry per PR, and #8 did not add one either.

**Plan drift:** no plan was recorded. The four changed files are exactly the ones listed under "Touches" in the issue. One piece of planned proof is missing from the gate report: a run of `factory dashboard` showing it changes no labels, reviews, branches or merge state. The test partly stands in for it, since GitHub is mocked and it checks the events journal is only appended to. Nothing in the diff adds a way to change state, so this gap does not hide a defect.

**Nits:**
- **Dead `reason` field:** `reason` on a non-escalated row is never set. Review-result events have no `reason` field (`dispatch.py:363`), and escalation overrides the state anyway. So the `pr.reason` check for `review_pending` items in the inbox can never be true.
- **Stalled PRs missing from the inbox:** dispatch does not retry a head whose review failed or was skipped as `prompt_too_large`. Such a PR has a `review-intake` event for the current head but no `review-result`. It stays in "pending review" and never reaches the inbox, even though the README promises that pending reviews needing human attention appear there.

```json
{"verdict":"APPROVE","findings":[{"severity":"nit","pass":"bug","path":"factory/dashboard.py","line":857,"summary":"`current.get(\"reason\")` is always None: review-result events never carry a `reason` (dispatch.py:363), and an escalation reason always comes with state `escalated`. So the `pr.reason` check for `review_pending` in dashboard.html:1162 can never be true and can be removed."},{"severity":"nit","pass":"spec","path":"factory/dashboard.html","line":1162,"summary":"A current head whose automated review failed or was skipped as prompt_too_large (it has a `review-intake` event for that head but no `review-result`) is never retried by dispatch, yet it stays `review_pending` and stays out of Inbox. The README's 'pending reviews needing human attention' and the issue's 'actionable stalled items' would cover it."}]}
```
VERDICT: APPROVE

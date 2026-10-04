# Flow

An issue becomes a pull request, and a pull request becomes a merge, only when the state and the agent rules allow it. Many issues stop earlier.

This is the happy path through `factory dispatch`. Stage detail and operator recovery are in `README.md` "How a ticket moves".

## Issue in

1. A human or another tool opens an issue. The template sections are Scope, Touches, Exit gate, and Out of scope (`.github/ISSUE_TEMPLATE/agent_task.md`).
2. `needs-triage` is the normal entry. `factory triage` either applies `ready-for-agent`, `needs-info`, or `ready-for-human`, or comments a `wontfix-proposal`. Bodies under 80 characters or without acceptance criteria become `needs-info` before the model runs (`factory/triage.py`).
3. Optional `needs-viability` is a manager recommendation. A BUILD adds `needs-triage`. It does not add `ready-for-agent`.
4. The dispatcher claims only `ready-for-agent` issues that are open, unassigned, unblocked, and not initiatives.

## Pull request out

Inside the claim, still on `agent/<n>`:

1. Worker attempts until the gate passes or attempts, budget, or a later review bounce fail.
2. The dispatcher pushes `agent/<n>` and opens a pull request against `[repo].main` with `Closes #<n>`, or updates the existing one. No commits ahead of main means no pull request, then escalation.
3. The reviewer runs. Findings are posted on the pull request.
4. `APPROVE` plus matching evidence adds `factory-approved` and journals `approved`. Otherwise the ticket escalates to `ready-for-human`, drops the assignee and `ready-for-agent`, and keeps the worktree.

The claim pass stops there. It does not merge.

## Merged pull request out

A later pass enters the merge stage first. One candidate per pass. All of the following are required (`land_pass`, `merge_pass_locked`):

- Head branch is `agent/<n>`, not a draft, base is the configured main branch.
- The issue is not an initiative.
- Label `factory-approved` is present.
- No human changes-requested review.
- At least one passing GitHub check, none failing or pending, rechecked after the other evidence.
- Head contains current main. If it is behind, this pass refreshes, re-gates, force-pushes with lease, reviews again, and returns. It does not merge on that pass.
- The journal has a passing gate and an accepted `APPROVE` for that exact head, and an `approved` event whose ticket, pull request, head, gate head, and review head are that same SHA.
- If `manager.review` is `"all"`, a manager `APPROVE` for that head as well. The default is `"escalated"`, so this extra wait is off unless configured.
- A final re-read still matches, then `gh pr merge --match-head-commit <sha>`.

Method is squash, unless the branch carries upstream commits that are not in main; then it is a merge commit.

After merge, retention runs. The worktree and local branch are removed only when retention says cleanup is safe.

## Other exits

These are normal outcomes, not failures of the architecture:

- `needs-info` or `ready-for-human` from triage.
- Escalation after budget, repeated gate failure, review rounds, empty branch, rebase conflict, red CI, or unbound approval.
- Manager `CLOSE`, `HUMAN`, or a handoff comment. A handoff reply is context. Re-label `ready-for-agent` to retry.
- CI pending. The pass waits. It does not merge and does not escalate.
- A `factory-approved` label with no journal row. The merge stage removes the label and escalates.

Re-queue by putting `ready-for-agent` back and clearing the assignee. The next pass reads that label. It does not resume an in-memory job.

# Dispatcher

`factory dispatch` is one pass. It exits. The next invocation starts by reading GitHub and `.factory/` again.

The code calls this stateless (`factory/cli.py`; `factory/dispatch.py` docstring and argparse description). That means no in-process session. It does not mean the pass has no durable state. The pass writes GitHub and `.factory/`, and a later pass trusts those writes.

Running it again is safe. Held locks are skipped. The merge lock is not waited on. The next timer or manual pass retries.

## One pass, in order

When `factory dispatch` is invoked without `--ticket` (`factory/dispatch.py` `main`):

1. **Landing.** `land_pass` takes `.factory/locks/merge.lock` or skips. Under that lock: upstream sync if `[repo].upstream` is set, then the merge stage. Upstream sync never moves `main`: when upstream moved and no `upstream sync:` issue is open, it stages the merge (or the conflict-free prefix) in a detached worktree, gates it, files one `ready-for-agent` sync ticket with that evidence, and hands the candidate to `agent/<n>`; the ticket then runs the claim, worker, gate, review, PR, and merge steps like any other. At most one pull request merges. A refresh of a behind pull request also returns after that one PR.
2. **Review intake.** `review_intake_pass` reviews opted-in pull requests. It does not merge them.
3. **Manager.** `manage_pass` from `factory/manage.py`. Viability, pull-request frontier, and escalation recovery run only when `manager.command` is set. Terminal human handoffs run either way.
4. **Claim.** Count held ticket locks. Capacity is `dispatch.max_active` minus that count (default `max_active` is 2). Claim that many open, unassigned, unblocked `ready-for-agent` issues that are not initiatives.

`--ticket N` skips landing, review intake, and manage. It processes that open issue even if it is assigned or missing `ready-for-agent`. It still refuses an initiative, a bad plan binding, or an open `agent/N` PR.

`--dry-run` prints planned actions and does not take the merge lock or claim.

`factory install` runs this pass on a systemd user timer. Default interval in config is 10 minutes. A manual `factory dispatch` during a timer pass skips contended locks instead of stacking work.

## Claim rules

`frontier` lists open issues with `ready-for-agent`. It skips initiatives, issues with assignees, issues with an open same-repository `agent/<ticket>` PR, and issues with open blockers. The PR hold names the PR (`approved, awaiting merge` when approved), regardless of assignment or requeue instructions, and ends when it merges or closes. Matching fork branch names do not confer ownership: those PRs are not held, closed or reopened as Factory delivery. Blockers come from the GitHub blocked-by API when present, and from `Blocked by: #N` lines in the body.

`process_ticket` re-reads the issue and open PRs before claiming. A stale list must not reclaim an escalated issue or replace an open PR's head. On every exit, including approval, dispatch releases only the assignment it added and journals `assignment-cleared` with ticket, login and exit path. Pre-existing human assignments remain untouched, including on a hand run. Factory issue filing does not assign anyone.

Automatic conflict, gate, risk and deferred-review requeues, plus manager RETRY, REWRITE, ROUTE and AUTHORIZE, close the agent PR before returning the ticket to `ready-for-agent`. The next claim remains on the same branch; publishing removes stale approval and reopens the latest closed, unmerged same-repository PR to retain its human reviews. If GitHub refuses reopening (including after head-branch deletion), publishing pushes the branch and creates a new PR requiring fresh approval. A failed closure does not release a ready requeue.

## Where the worker runs

After claim, the pass creates or reuses `.factory/wt-<n>` on `agent/<n>`. See [Agents](agents.md). The same pass gates, pushes, opens or updates the pull request, reviews, and may bounce. It does not merge that pull request in the same claim. Merge is the start of a later pass.

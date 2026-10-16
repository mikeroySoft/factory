# Dispatcher

`factory dispatch` is one pass. It exits. The next invocation starts by reading GitHub and `.factory/` again.

The code calls this stateless (`factory/cli.py`; `factory/dispatch.py` docstring and argparse description). That means no in-process session. It does not mean the pass has no durable state. The pass writes GitHub and `.factory/`, and a later pass trusts those writes.

Running it again is safe. Held locks are skipped. The merge lock is not waited on. The next timer or manual pass retries.

## One pass, in order

When `factory dispatch` is invoked without `--ticket` (`factory/dispatch.py` `main`):

1. **Landing.** `land_pass` takes `.factory/locks/merge.lock` or skips. Under that lock: upstream sync if `[repo].upstream` is set, then the merge stage. At most one pull request merges. A refresh of a behind pull request also returns after that one PR.
2. **Review intake.** `review_intake_pass` reviews opted-in pull requests. It does not merge them.
3. **Manager.** `manage_pass` from `factory/manage.py`. Viability, pull-request frontier, and escalation recovery run only when `manager.command` is set. Terminal human handoffs run either way.
4. **Claim.** Count held ticket locks. Capacity is `dispatch.max_active` minus that count (default `max_active` is 2). Claim that many open, unassigned, unblocked `ready-for-agent` issues that are not initiatives.

`--ticket N` skips landing, review intake, and manage. It processes that open issue even if it is assigned or missing `ready-for-agent`. It still refuses an initiative and a bad plan binding.

`--dry-run` prints planned actions and does not take the merge lock or claim.

`factory install` runs this pass on a systemd user timer. Default interval in config is 10 minutes. A manual `factory dispatch` during a timer pass skips contended locks instead of stacking work.

## Claim rules

`frontier` lists open issues with `ready-for-agent`. It skips initiatives, issues with assignees, and issues with open blockers. Blockers come from the GitHub blocked-by API when present, and from `Blocked by: #N` lines in the body.

`process_ticket` re-reads the issue before `gh issue edit --add-assignee @me`. A stale list must not reclaim an issue that was just escalated.

## Where the worker runs

After claim, the pass creates or reuses `.factory/wt-<n>` on `agent/<n>`. See [Agents](agents.md). The same pass gates, pushes, opens or updates the pull request, reviews, and may bounce. It does not merge that pull request in the same claim. Merge is the start of a later pass.

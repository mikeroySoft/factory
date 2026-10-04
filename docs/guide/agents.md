# Agents

Four roles exist in the current code. They do not share a process. Each is an argv from configuration, run inside a pass, against GitHub and `.factory/`.

## Worktrees

`ensure_worktree` (`factory/dispatch.py`) uses `.factory/wt-<n>` and branch `agent/<n>`.

- Reuse the directory if it exists.
- If the local branch exists, add a worktree on it.
- Otherwise start from `origin/agent/<n>` if that ref exists.
- Otherwise start from `origin/<main>` and create `agent/<n>`.

The worker prompt forbids pushing, merging, or fast-forwarding the configured main branch, and forbids `git stash`. Only the merge stage moves main. Standing text is `STANDING_INSTRUCTIONS` in `factory/dispatch.py`.

Workers are chosen by ticket labels. First matching key in `[workers]` wins; otherwise `default`. Optional host-only `worker_wrap` prefixes every worker argv. A committed `[worker_wrap]` is refused.

## Locks

| Lock | Scope | Contended behavior |
|---|---|---|
| `.factory/locks/<n>.lock` | One ticket number, or a review-intake pull request number | Skip. Retry next pass. |
| `.factory/locks/merge.lock` | Upstream sync and merge stage | Skip. Retry next pass. |
| `gate.lock`, default `/tmp/factory.lock` | Host-wide exclusive gate checks | Block until acquired. A timed-out check fails and releases it. |

`max_active` counts locks that are actually held, not worktree directories. Removing a worktree does not release its lock. The skill states this in `skills/factory/SKILL.md`.

The ticket lock is held for the whole `process_ticket` pipeline, then released. An escalated worktree is kept. Cleanup after a successful merge removes the worktree and deletes the lock file only if handoff retention reports `cleanup_safe` (`cleanup_after_merge`).

## Worker

The worker runs in the worktree with a prompt file. The dispatcher, not the worker, opens the pull request. Attempts are `dispatch.max_attempts` (default 3) inside `dispatch.budget_min` (default 90). Each failed gate is fed back as the next prompt. Gate failure on the last attempt escalates.

## Reviewer

The reviewer is a separate argv (`[review].command`). It reviews `git diff origin/<main>..HEAD`. It is told not to run builds or tests. The gate report is the build and test evidence.

A review counts only when all of these hold (`review` in `factory/dispatch.py`):

- HEAD before and after the review equals the commit that passed the gate.
- The reviewer exits 0.
- The output contains exactly one `VERDICT: APPROVE` or `VERDICT: REVISE` line, and that line is the end of the output.

Anything else is `REVISE`. That includes a nonzero exit, a missing verdict, extra verdict lines, and a HEAD that moved.

`APPROVE` does not merge. `approve_pr` adds `factory-approved` only while the open pull request still points at that head, targets the configured main branch, and has no changes-requested review. It then journals `approved` with the same SHA. If `manager.review` is `"all"`, the label waits for a manager `APPROVE` bound to that head.

`dispatch.review_rounds` (default 1) is how many times a `REVISE` goes back to the worker. After that, the ticket escalates.

## Manager

The manager exists only when `manager.command` is set. `factory manage` and the dispatch pass call `manage_pass`.

Order inside `manage_pass`:

1. Viability, if a manager is configured. Pull requests before issues. Recommendations only. See `README.md` "Opt-in viability recommendations" and `skills/factory/SKILL.md`.
2. Pull-request frontier, if a manager is configured. Same-repo `agent/<n>` pull requests linked to a factory issue. Not initiatives. Not already escalated.
3. Escalation recovery, if a manager is configured, for untouched `ready-for-human` packets within `manager.rounds` (default 1).
4. Routed human handoff. This runs even when no manager is configured.

Escalation decisions the parser accepts: `RETRY`, `REWRITE`, `SPLIT`, `ROUTE`, `FIX`, `HUMAN`, `CLOSE`. `APPROVE` is accepted only on the frontier when `manager.review` is `"all"`. Default `manager.review` is `"escalated"`, which does not add that approval gate.

Unparseable manager output becomes `HUMAN`. A nonzero manager exit is recorded as `manager_failed` and does not consume another product round. A failed GitHub mutation after a decision is not replayed. Human timeline activity after the escalation stops the manager from applying a decision.

`docs/manager-plan.md` is a research plan. Do not treat its later phases as shipped behavior. Shipped behavior is `factory/manage.py` and the manager sections of `README.md`.

## Fail closed

These are current refusal rules, not a wishlist.

- Malformed review evidence is `REVISE`.
- Merge with no passing GitHub check is refused. Unparseable `gh pr checks` output is treated as no passing check.
- Missing, unbound, or stale journal approval removes `factory-approved` and escalates.
- A human `CHANGES_REQUESTED` review blocks merge.
- A pull request whose base is not `[repo].main` is not pushed, refreshed, or merged.
- An initiative is not executed, managed, or merged.
- A linked ticket whose body no longer matches its accepted plan baseline is refused.
- Exclusive gate checks time out instead of holding the host lock forever.
- The merge stage uses `gh pr merge --match-head-commit`. That guards the head SHA. It does not by itself stop a retarget after the last read. Protect the release branch on GitHub (`README.md`).

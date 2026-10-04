# State

Runtime control and evidence live in GitHub and in a gitignored directory on the main checkout. The dispatcher process itself stores nothing between runs.

Source: `factory/dispatch.py` module docstring; `factory/config.py` `Config.factory`; `.gitignore`; `skills/factory/SKILL.md`.

## GitHub

Labels are the control plane. `factory init` creates them. Names are fixed in `factory/config.py` `LABELS`, not in `.factory.toml`.

| Label | Role |
|---|---|
| `needs-viability` | Ask the manager for a build, defer, or do-not-build recommendation on an issue. |
| `needs-review` | Ask the manager for a direction recommendation on a pull request. Also admits review intake. |
| `needs-triage` | Waiting for `factory triage`. |
| `needs-info` | Triage rejected the body and asked a question. |
| `ready-for-agent` | Claimable by the dispatcher. |
| `ready-for-human` | Escalated. Not claimable. |
| `factory-approved` | Reviewer approval recorded for one head. Merge-stage precondition. Not sufficient alone. |
| `chore` | Selects the chore worker. |
| `wontfix-proposal` | Proposal only. Triage never applies `wontfix`. |
| `initiative` | Plan record. Never triaged, dispatched, managed, or merged. |

Comments are receipts: triage briefs, escalation reasons, reviewer findings, manager decisions, handoff requests.

Pull requests on `agent/<n>` are the work product. The merge stage reads their head SHA, base branch, checks, labels, and review decision.

Issue bodies also carry contracts: acceptance criteria, `Blocked by: #N`, and optional initiative bindings. See `README.md` "How a ticket moves" and "Immutable initiative bindings".

## Gitignored `.factory/`

`Config.factory` is `<main checkout>/.factory`. `factory init` adds `/.factory/` and `.factory-prompt.md` to `.gitignore`.

Typical contents:

| Path | Role |
|---|---|
| `.factory/locks/<n>.lock` | Per-number `flock`. Lives outside the worktree so deleting the worktree does not drop the lock. |
| `.factory/locks/merge.lock` | Serializes upstream sync and the merge stage. |
| `.factory/wt-<n>/` | Git worktree for branch `agent/<n>`. |
| `.factory/logs/` | Worker logs. |
| `.factory/events.jsonl` | Journal. Rotates. Merge eligibility reads it. |
| `.factory/wt-<n>/.factory/gate-report-<n>.md` | Gate report inside the worktree. |
| `.factory/wt-<n>/.factory/handoff-<n>.md` | Worker handoff notes. Gitignored. |
| `.factory/manager/notes.md` | Optional manager notes. Gitignored. |

Review intake locks `.factory/locks/<pr>.lock` using the pull request number, not the issue number (`factory/dispatch.py` `review_intake_pass`).

## Not runtime state

These are easy to confuse with `.factory/`:

- `.factory.toml` at the repo root is committed configuration. The host file `$XDG_CONFIG_HOME/factory/config.toml` layers over it. Gate checks, leak scan, and upstream do not come from the host file (`factory/config.py`).
- `.factory-lessons.md` is committed text written by `factory learn`. Workers read it. It is not the journal.
- The host gate lock defaults to `/tmp/factory.lock`. It is not inside `.factory/`.

## What is authoritative

For "what should run next", read GitHub labels and assignees, then re-read the issue before claiming. Listings lag.

For "may this head merge", read the journal and the live pull request together. A `factory-approved` label without a matching journal row is withdrawn. See [Flow](flow.md).

Deleting `.factory/` does not make the dispatcher stateless in a useful way. It drops locks, worktrees, logs, and the approval evidence the merge stage requires.

---
name: factory
description: Operate the factory pipeline (label-driven GitHub issues → worker agents in worktrees → deterministic gate → reviewer → merge stage). Use when the user asks to set up the factory in a repository, assess an issue or PR direction's viability, write or split a ticket for the factory, check why a ticket escalated to ready-for-human or stalled, read the factory dashboard, or change gate checks, workers, or the reviewer in .factory.toml.
---

# factory

The `factory` CLI runs a ticket pipeline for one GitHub repository, configured by
`.factory.toml` at its root. State lives in GitHub labels/comments/PRs and a
gitignored `.factory/`. `factory --help` lists commands; `factory <cmd> --help` lists
options.

First, ensure the tool is installed: `factory --version`. If absent, install it with
the first available of `uv tool install git+https://github.com/mikeroySoft/factory@stable`,
`pipx install git+https://github.com/mikeroySoft/factory@stable`, or
`python3 -m pip install --user git+https://github.com/mikeroySoft/factory@stable`
(Linux, Python ≥ 3.11; no dependencies). These URLs explicitly select the released
`stable` channel. Replace `@stable` with `@main` for development or `@v0.3.0`
for a fixed release. A bare URL follows the default `main` branch.
Confirm with `factory --version` before continuing. Then pick the branch:

- Repo has no `.factory.toml` → **Set up**.
- User asks whether an issue or PR direction is worth pursuing → **Assess viability**.
- User wants work done by the factory → **Write a ticket**.
- A ticket is `ready-for-human`, stuck, or the user asks "what happened" → **Diagnose**.
- User wants different checks, workers, reviewer, or model → edit `.factory.toml`
  (the template written by `factory init` documents every key), then `factory doctor`.

## Set up

1. `factory init` in the repo root. Read the `[[gate.check]]` section it wrote and
   replace the placeholder with the repo's real lint/test commands — take them from
   CI config (`.github/workflows/*.yml`), `Makefile`, `package.json` scripts, or
   `justfile`; do not invent commands. Mark checks that need a GPU or other
   single-tenant hardware `exclusive = true`.
2. The merge stage refuses to land a PR with no passing GitHub check. If the repo had
   no workflow, `init` wrote `.github/workflows/ci.yml` with a placeholder step:
   ask the user to confirm, then make that step run the same commands as
   `[[gate.check]]` (with whatever toolchain setup the runner needs). If the repo
   already has CI, confirm it runs on `pull_request`; if it does not, ask before
   adding a trigger.
3. If the repo is a fork that must track upstream, set `[repo].upstream` to the
   remote name and confirm the remote exists.
4. Commit `.factory.toml`, `.gitignore`, `.github/ISSUE_TEMPLATE/agent_task.md`,
   and `.github/workflows/`.
5. `factory doctor` must print `OK: 0 blocking problem(s)`. Fix every FAIL; report
   each WARN to the user with what it disables (triage endpoint offline → no
   triage; timer missing → nothing runs unattended; `github workflow` WARN → the
   merge stage will never land a PR).
6. `factory install --dashboard` when the user wants it unattended; otherwise
   tell them `factory dispatch` runs one pass by hand.

Done when `factory dispatch --dry-run` runs without error and prints the frontier.

## Assess viability

Use the existing `[manager].command` in read-only/no-tools mode. Without it,
viability is disabled. This stage recommends direction; it does not write specs
or perform code-quality review.

1. On an existing installation, `factory init --labels-only` provisions
   `needs-review` and `needs-viability` through the normal label registry.
2. With authorization for the exact target, opt a PR in with
   `gh pr edit N --add-label needs-review`, or an issue with
   `gh issue edit N --add-label needs-viability`. Unlabeled issues are not swept.
3. `factory manage --dry-run` lists eligible requests without inference or writes.
   `factory manage` (also the dispatch manager stage) processes PRs before issues.
   Held, closed, conflicting-stage, and locked targets are skipped.
4. Read the target's `Factory manager:` comment and final
   `VERDICT: BUILD|DONT_BUILD|DEFER`, with evidence citations and cost/risk reasoning.
   All verdicts consume the opt-in label. An issue BUILD adds `needs-triage`
   for deeper investigation, never `ready-for-agent`. Negative/deferred issues
   stay open without `wontfix`; the human closes or overrules.
   PR verdicts are recommendation-only, even BUILD: no handoff label until
   factory #5, and no quality review, approval, merge, or closure.
5. If application failed, inspect `.factory/events.jsonl`'s `viability` event
   and live state: the full comment is recorded before mutations and that
   label-add request is never automatically replayed. Recover manually rather
   than duplicating a potentially posted comment. To request a new assessment,
   remove and re-add the label; each label-add timeline ID is a new request.

Done when the cited verdict is visible and the opt-in label is consumed (plus
`needs-triage` for an issue BUILD), or a failed application is handed to the
human with the recorded verdict and observed live state.

## Write a ticket

A ticket the factory can work is one observable change with a checkable exit gate.
Triage rejects bodies under 80 characters or without acceptance criteria, so the
issue template's four sections are the contract:

- **Scope** — the outcome, observable from outside.
- **Touches** — files/symbols expected to change (pointers, not a contract).
- **Exit gate** — the condition that means done AND the exact command that
  verifies it. A test the worker must make pass is the strongest form.
- **Out of scope** — what stays untouched.

Split anything with two outcomes into two tickets. Order them with a `Blocked by: #N`
line (the dispatcher skips blocked tickets until the blocker closes). Add the `chore`
label for mechanical work (renames, doc sync, bumps) to route it to the chore worker.
File with `gh issue create --label needs-triage`; label `ready-for-agent` directly
only when the user says to skip triage.

Done when the issue carries `needs-triage` (or `ready-for-agent`) and every section is filled.

## Diagnose

Evidence, in order of authority:

1. `.factory/events.jsonl` filtered to the ticket: the ordered trail of `claimed`,
   `attempt` (worker exit, gate PASS/FAIL, seconds, log path), `review` verdicts,
   `approved`, `merged`, `escalate` with the terminal reason.
2. The issue's last factory comment: `Factory dispatcher escalating: <reason>` plus
   the worker's handoff notes (what it changed, what it left unverified).
3. The gate report: `.factory/wt-<n>/.factory/gate-report-<n>.md` — PASS/FAIL per
   check with the failing tail. On the PR, the same report is in the body.
4. The reviewer's findings: PR comments ending in `VERDICT: …`; each finding cites
   `path:line`.
5. The worker log: `.factory/logs/<n>-attempt-<k>.log` (attempt 4 is the review
   bounce), and `.factory/wt-<n>/.factory/handoff-<n>.md`.
6. The dispatcher pass: `journalctl --user -u factory-<repo>.service -n 200`, or the
   dashboard's journal heartbeat.
7. `factory dashboard --json` for the whole state in one document; `factory stats`
   for attempts/rounds/hours per ticket.

Then act at the cause: a gate failure that is the ticket's fault → fix the ticket
(sharpen the exit gate) and re-label `ready-for-agent`; a gate failure from a
wrong check command → fix `.factory.toml`; reviewer disagreement → read the
findings and either amend the ticket or take the branch over by hand
(`git worktree` at `.factory/wt-<n>`, branch `agent/<n>`); red CI or a failed
refresh (rebase conflict, nothing ahead of main, gate failed after rebase) → the
label `factory-approved` was removed on purpose; resolve, then re-add it to
re-enter the merge stage. A human blocks any merge by requesting changes on the PR. When the same
cause shows up across tickets, run `factory learn --dry-run`, check the proposed
lessons against the evidence, then `factory learn` and commit `.factory-lessons.md`.

Done when the ticket is re-labelled with a stated reason, or handed to the user with
the cause and the evidence path.

## Invariants the factory relies on

- Only the merge stage moves `main`; workers push `agent/<n>` only.
- A merge needs all four: gate PASS, `factory-approved`, green CI, head contains
  `main`. One PR per pass; behind-main PRs are rebased and re-gated first.
- `wontfix` is proposed by triage, never applied.
- Viability consumes explicit opt-in labels, PRs before issues. Only issue BUILD
  can add `needs-triage`; PR verdicts have no handoff or review authority.
- Concurrency is the per-ticket `flock` in `.factory/locks/`; `max_active` counts held
  locks. Removing a worktree does not release its lock.

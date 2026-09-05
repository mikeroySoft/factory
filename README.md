# factory

A label-driven autonomous ticket pipeline for any GitHub repository. Issues
labelled `ready-for-agent` are claimed by a coding agent in a git worktree,
gated by your own deterministic checks, reviewed by a second model, opened as a
PR, and landed on `main` once the gate, the reviewer, GitHub CI, and freshness
against `main` all agree. Anything the pipeline cannot resolve is handed back
with a `ready-for-human` label and the evidence attached.

It runs on your machine, on a systemd timer, with the agent CLIs you already
have. State is GitHub (labels, comments, PRs) plus a gitignored `.factory/`
directory; the dispatcher itself is stateless and safe to re-run.

```
 needs-triage ──factory triage──▶ ready-for-agent ──factory dispatch──▶ agent/<n> PR ──merge stage──▶ main
                    │                                     │                  │
                    ▼                                     ▼                  ▼
                needs-info                          ready-for-human      factory-approved
```

## Install

Two ways in; both end with the same `factory` CLI on your machine.

**Have your coding agent do it:** install the skill and ask the agent to set up
the factory in your repo. The skill installs the CLI if it is missing, writes
the config from your CI, and runs the doctor.

```sh
npx skills add mikeroysoft/factory
```

**Or by hand.** Linux, Python ≥ 3.11, `git`, `gh` (authenticated with push access), and:

- a **worker** agent CLI that accepts a prompt file and works in a directory
  (default `omp -p`; `droid`, `codex exec`, `claude -p`, … work the same way)
- a **reviewer** CLI that answers a prompt on stdout (default `omp -p --model anthropic/claude-fable-5-1`; `codex exec` works the same way)
- optionally, an OpenAI-compatible local model for triage (Ollama, vLLM,
  llama.cpp, LM Studio)

```sh
uv tool install git+https://github.com/mikeroySoft/factory   # or: pipx install ...
```

No Python dependencies.

## Set up a repository

```sh
cd your-repo
factory init            # .factory.toml, .gitignore, issue template, labels
$EDITOR .factory.toml   # put your real test/lint commands in [[gate.check]]
git add .factory.toml .gitignore .github/ISSUE_TEMPLATE/agent_task.md && git commit
factory doctor          # tools, auth, remotes, model endpoint
factory install --dashboard   # systemd user timer every 10 min + dashboard on :8765
```

Then file an issue with the **Agent task** template (Scope / Touches / Exit
gate / Out of scope). It gets `needs-triage`; the next pass triages it; if it is
fully specified it becomes `ready-for-agent` and is picked up.

For a fork that tracks an upstream, set `[repo].upstream = "upstream"` and the
dispatcher merges new upstream commits into your `main` (gated) before each
merge stage.

## Commands

| Command | What one invocation does |
|---|---|
| `factory triage` | Labels every `needs-triage` issue via the local model: `ready-for-agent` (with an agent brief), `needs-info` (with the question), `ready-for-human`, or a `wontfix` proposal comment. `--dry-run`, `--issue N`, `--replay a,b,c`. |
| `factory dispatch` | One stateless pass: upstream sync → merge stage (at most one PR) → claim up to `max_active` tickets → worker → gate → PR → review → up to `review_rounds` bounces. `--ticket N` forces one issue; `--dry-run` prints the plan. |
| `factory gate` | Runs the deterministic gate in the current worktree and writes a Markdown report. Workers run it themselves; the dispatcher re-runs it as the evidence of record. |
| `factory stats` | Ticket table: attempts, review rounds, hours to merge, escalation count, resolver attribution, minutes in `ready-for-human`, and re-queues. Reads GitHub plus existing `events.jsonl`. `--json`. |
| `factory learn` | Reads the last N finished tickets' event trail, failing-attempt log tails, reviewer findings, and escalation reasons; asks the local model for ≤10 repo-specific lessons; writes `.factory-lessons.md` (you commit it). Every worker prompt carries it. `--dry-run`, `--last N`. |
| `factory dashboard` | Local ops UI: tickets by stage, in-flight phase, gate reports, worker logs, journal heartbeat, upstream drift, and an action list with one-click answers. `--host 0.0.0.0` exposes it (and its mutating `/api/act`) to your network. |
| `factory doctor` / `init` / `install` | Onboarding, above. |

Every command reads `.factory.toml` from the main checkout, even when run
inside one of its worktrees.

Human-touch metrics are read-only; no manager behavior is required. A
`ready-for-human` label addition starts an escalation interval; removal ends it
and attributes the resolution to that removal's actor (`User` → human, `Bot` →
factory, absent/other → unknown). Resolver logins are retained. Automation using
a human account is indistinguishable from manual activity under that account.
Open intervals accrue until now, or until closure/merge for finished tickets.
Re-queues count `ready-for-agent` additions after the initial queue entry, with
repeated `claimed` trace records as a fallback. Trace escalation counts likewise
supplement timeline counts without adding the two counts together.

The stats footer and dashboard KPIs show escalations in the trailing seven days
and the percentage of attributed resolutions performed by humans; unresolved
and unknown resolutions are excluded from that denominator (`n/a`/`null` when
none are attributed). `factory dashboard --json` exposes
`metrics.escalations_per_week`, `metrics.human_resolved_pct` (0–100), and each
ticket's `human_touch` details. The dashboard retains its existing 100-issue,
100-PR, and 100-timeline-item query limits; stats paginates label timelines.

## How a ticket moves

1. **Triage.** A deterministic lint rejects bodies under 80 characters or
   without acceptance criteria (`needs-info` with a specific question). The
   model then decides between the four labels; `wontfix` is only ever proposed.
2. **Claim.** The dispatcher re-reads the issue (search-backed listings lag),
   assigns itself, takes a per-ticket `flock`, and creates the worktree
   `.factory/wt-<n>` on branch `agent/<n>`.
3. **Work.** The worker gets the issue, its comments, standing instructions
   (commit incrementally, never touch `main`, never `git stash`, finish with
   `factory gate`), and — on retries — the previous gate report or the
   reviewer's findings. Up to `max_attempts` rounds within `budget_min`.
4. **Gate.** `conflict-markers`, your `[[gate.check]]` list in order, then a
   `leak-scan` of added lines against a regex. Checks marked `exclusive`
   serialise on a host-wide lock (one GPU, many worktrees). Every check has a
   timeout; a wedged check fails instead of holding the lock.
5. **Review.** The reviewer sees the diff, the issue, and the gate report;
   every finding must cite `path:line`; it ends with `VERDICT: APPROVE` or
   `VERDICT: REVISE`. Each `REVISE` goes back to the worker with the findings
   (re-gate, push, re-review), up to `review_rounds` times; then it escalates.
   `APPROVE` adds the `factory-approved` label — durable evidence on the PR,
   not in memory.
6. **Merge stage** (start of the next pass). One PR per pass, requiring all
   four: gate PASS in the PR body, `factory-approved`, green GitHub checks
   (fail-closed on missing or unparsable checks), and a head that already
   contains the current `main` tip. Behind `main` → rebase, re-gate on this
   host, force-push, merge next pass. Red CI → label removed, escalated once
   with the failing check names. A human blocks any merge by requesting
   changes on the PR.
7. **Escalation.** Budget exceeded, gate failed thrice, second `REVISE`,
   nothing to PR, rebase conflict, red CI: the issue gets `ready-for-human`,
   loses the assignee and `ready-for-agent`, and receives a comment with the
   reason and the worker log path. The worktree is kept for forensics.

## Configuration

`.factory.toml` at the repository root; every key is optional. The template
written by `factory init` documents them all. The ones you will actually set:

```toml
[repo]
# upstream = "upstream"          # fork workflow: sync upstream main each pass

[dispatch]
review_rounds = 1                # REVISE -> worker -> re-review cycles
# cost_pattern = 'Total cost:\s*\$([0-9.]+)'   # $ from the worker log (Claude Code prints this)

[workers]                        # ticket label -> argv; {prompt} file, {cwd} worktree
default = ["omp", "-p", "--cwd", "{cwd}", "@{prompt}"]
chore   = ["droid", "exec", "-f", "{prompt}", "--auto", "medium", "--cwd", "{cwd}"]

[review]
command = ["omp", "-p", "--no-session", "--model", "anthropic/claude-fable-5-1", "{prompt}"]   # {prompt} = review prompt text

[gate]
timeout = 1200
lock = "/tmp/factory.lock"

[[gate.check]]
name = "lint"
run = ["cargo", "clippy", "--workspace", "--all-targets", "--", "-D", "warnings"]
exclusive = true

[[gate.check]]
name = "tests"
run = ["cargo", "test", "--workspace"]
exclusive = true

[leak_scan]
pattern = "internal|confidential|proprietary|private|jira|confluence|\\.corp|\\.internal"

[triage]
url = "http://127.0.0.1:11434/v1/chat/completions"
model = "qwen3:30b"
```

Labels (`needs-triage`, `needs-info`, `ready-for-agent`, `ready-for-human`,
`factory-approved`, `chore`) and the `agent/<n>` branch scheme are fixed
conventions; `factory init` creates the labels.

## Operating it

- **Dashboard** (`factory dashboard`): the *actions* list is the inbox —
  escalations, `needs-info` questions, wontfix proposals, parked upstream
  syncs, a dead timer — each with the exact command or a button.
- **Spend**: the *Spend* KPI and each ticket's attempts tab total worker+gate
  wall clock from `events.jsonl`, plus dollars when `cost_pattern` matches
  your worker's log.
- **Learning loop**: after a batch of tickets, `factory learn --dry-run`,
  read the proposed lessons, then `factory learn` and commit
  `.factory-lessons.md`. Workers see it on every ticket. The eval signal is the
  dashboard's *first-gate pass* and *bounce rate* KPIs moving after the change;
  edit or delete lessons that don't earn their keep.
- **Audit trail**: `.factory/events.jsonl` — one row per stage transition
  (`claimed`, `attempt` with worker exit/gate result/seconds, `pr-opened`,
  `review` verdict, `approved`, `refreshed`, `merged`, `escalate` with reason,
  `upstream-sync`). `jq 'select(.ticket==42)' .factory/events.jsonl` is a
  ticket's whole history without a GitHub call.
- **Handoff notes**: each worker attempt ends by writing
  `.factory/wt-<n>/.factory/handoff-<n>.md` (what changed, what is unverified,
  what next). The next attempt gets it in its prompt; an escalation quotes it
  in the issue comment.
- **Logs**: `.factory/logs/<n>-attempt-<k>.log` per worker round;
  `.factory/wt-<n>/.factory/gate-report-<n>.md` per gate;
  `journalctl --user -u factory-<repo>.service` for dispatcher passes.
- **Re-run one ticket by hand**: `factory dispatch --ticket N` (bypasses the
  frontier and its label checks; respects the in-flight lock).
- **Stop everything**: `systemctl --user disable --now factory-<repo>.timer`.
  In-flight tickets finish their current pass; nothing new is claimed.
- **Tear down a ticket**: remove the worktree (`git worktree remove --force
  .factory/wt-<n>`), delete `agent/<n>`, and re-label the issue.

## Agent skill

`skills/factory/SKILL.md` teaches a coding agent to install the factory
in a repo, write tickets it can actually work, and diagnose escalations:

```sh
npx skills add mikeroysoft/factory
```

## Architecture

`factory/architecture.html` (served by the dashboard at `/atlas`) shows
the system and the ticket lifecycle. Modules map 1:1 to commands:
`triage.py`, `dispatch.py`, `gate.py`, `stats.py`, `dashboard.py`,
`onboard.py`, with `config.py` as the single source of every repo-specific
value.

## License

MIT

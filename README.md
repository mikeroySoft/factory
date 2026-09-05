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
| `factory stats` | Ticket table: attempts, review rounds, hours to merge. `--json`. |
| `factory learn` | Reads the last N finished tickets' event trail, failing-attempt log tails, reviewer findings, and escalation reasons; asks the local model for ≤10 repo-specific lessons; writes `.factory-lessons.md` (you commit it). Every worker prompt carries it. `--dry-run`, `--last N`. |
| `factory dashboard` | Local ops UI: tickets by stage, authoritative in-flight phase when known, gate reports, worker logs, journal heartbeat, upstream drift, and an action list with one-click answers. `--json` prints the existing snapshot, including independent executions and local interruption reconciliation. `--host 0.0.0.0` exposes it (and its mutating `/api/act`) to your network. |
| `factory doctor` / `init` / `install` | Onboarding, above. |

Every command reads `.factory.toml` from the main checkout, even when run
inside one of its worktrees.

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

- **Dashboard** (`factory dashboard`): **Inbox** opens a full **Understand →
  Compare → Decide** briefing for each case needing human judgment. The question,
  situation, FM recommendation, relevant earlier decisions, uncertainty, options,
  consequences, and next owner stay visible; raw evidence is expandable. **Ops**
  retains the board, telemetry, dispatcher runs, and task drawers.
  **Ask FM** works on a whole task or a specific source/log and returns cited
  answers. It requires an authenticated `omp` installation; `[manager].model`
  chooses the model (host-wide: `[defaults.manager]`). If unset, an existing
  `manager.command` supplies only its `--model` value, otherwise OMP's default
  model is used. The dashboard never executes that command: questions run a
  bounded, read-only, no-tools OMP process against server-collected evidence.
  Evidence is sent to the selected model provider; questions are not posted to
  GitHub. Errors remain visible and retryable, never replaced with canned advice.
  Decisions require rationale and an exact mutation preview; stale or incomplete
  snapshots block execution. Confirmed decisions leave GitHub rationale comments
  and a local `human-decision` audit event with success, partial, or failed outcome.
  Drafts and conversations survive refresh within the same browser session.
- **Spend**: the *Spend* KPI and each ticket's attempts tab total worker+gate
  wall clock from `events.jsonl`, plus dollars when `cost_pattern` matches
  your worker's log.
- **Learning loop**: after a batch of tickets, `factory learn --dry-run`,
  read the proposed lessons, then `factory learn` and commit
  `.factory-lessons.md`. Workers see it on every ticket. The eval signal is the
  dashboard's *first-gate pass* and *bounce rate* KPIs moving after the change;
  edit or delete lessons that don't earn their keep.
- **Audit trail**: `.factory/events.jsonl` retains the ticket outcome events
  (`claimed`, `attempt`, `pr-opened`, `review`, `approved`, `refreshed`,
  `merged`, `escalate`, `upstream-sync`, and `human-decision`) alongside
  versioned `lifecycle` records. See the [event contract](#execution-event-contract)
  below. A ticket's history is local; no GitHub call is needed.
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

## Execution event contract

`.factory/events.jsonl` is the single append-only journal for legacy ticket
outcomes and authoritative execution evidence. An execution is one entered
scope, not a ticket's entire history. A dispatcher pass, an independent triage
run with no tickets, every worker attempt, and each later PR revisit have their
own identities. Parent/child scopes may overlap; never collapse them into the
newest ticket event.

### Version 1 lifecycle rows

Every `event: "lifecycle"` row includes all these keys. `null` means not
applicable or not known; it is not a fabricated ticket, run, round, or result.

| Key | JSON type | Meaning |
|---|---|---|
| `event` | string | Always `"lifecycle"`. |
| `schema_version` | integer | `1`; unversioned ticket events are not lifecycle version 1. |
| `event_id` | string (UUID) | Stable identity of this transition. Ordinary events use UUIDv4; reconciled interruption exits use a deterministic UUIDv5 per execution. |
| `sequence` | integer, ≥1 | Starts at 1, increases strictly within `execution_id`, allocated under the journal lock. |
| `execution_id` | string (UUID) | One stage invocation; never reused for a retry or revisit. |
| `parent_execution_id` | string (UUID) or null | Immediately enclosing execution, including across instrumented subprocess launches; null for an independent root. |
| `root_execution_id` | string (UUID) | Root execution of this causal tree; an independent execution names itself. |
| `dispatcher_run_id` | string (UUID) or null | Shared by a real dispatcher pass and its descendants. An independently invoked triage or gate has null, not an invented dispatcher run. |
| `ticket` | integer or null | Associated issue number; dispatcher, scheduling, and no-ticket triage scopes need no issue. |
| `attempt` | integer or null | Existing worker-attempt numbering, inherited by its gate. Review-bounce attempts retain the existing `max_attempts + bounce` numbering. |
| `review_round` | integer or null | Initial review is 1; revision worker/gate/review scopes use `bounce + 1`. Initial worker/gate scopes and unrelated activity have null. |
| `stage` | string | Actual entered scope, listed below; not inferred from labels or artifact times. |
| `kind` | string | `"enter"`, `"exit"`, or an evidence event listed below. |
| `at` | string | UTC source timestamp, ISO 8601 with microseconds and `Z`. A reconciled exit uses the time of observation, not an estimated crash time. |
| `outcome` | string or null | Terminal classification on `exit`; null on other kinds. |
| `reason` | string or null | Supported terminal reason or diagnostic text, not a closed enumeration; null when no reason is known. |
| `process` | object or null | Owner identity sampled on entry: `pid` (integer), `boot_id` (string or null), `start_ticks` (integer or null), `pid_namespace` (string or null), `uid` (integer or null), `state` (Linux process-state string or null), `ppid` (integer or null). Factory emits an object; the reader also accepts null as unavailable authority. This cached identity is not a live heartbeat. |
| `locks` | array of objects | Recorded exclusion evidence, possibly empty. Each object has `path` (absolute-path string), `device` (integer or null), and `inode` (integer or null). Null identity fields mean unavailable evidence. |

IDs and sequence numbers survive repeated reads. Sort a single execution by
`sequence`, use parent/root/run IDs for causality, and deduplicate by `event_id`.
Neither wall-clock timestamps nor physical append order establish a causal total
order across independent executions. A scope has one `enter` and at most one
terminal `exit`; an abrupt death can leave the exit absent until observation
has enough evidence to reconcile it. Extra keys and evidence kinds may be
added; consumers should ignore those they do not understand. Unsupported schema
versions are not interpreted as version 1 by the existing observer.

| Stage | Boundary |
|---|---|
| `dispatcher` | One real dispatcher pass, including passes with no ticket. Completion means the pass ended, not that any ticket merged. |
| `scheduling` | Frontier/capacity evaluation and scheduling; separate from ticket execution and merge eligibility. |
| `ticket` | A lock-owning ticket invocation, including admission re-read. Admission refusal is not worker time. Later PR passes have separate `merge-eligibility` executions. |
| `triage`, `triage-ticket` | Whole triage invocation and each actual ticket decision. Standalone triage retains its own root identity and null dispatcher association. |
| `worker` | One worker subprocess attempt. |
| `gate`, `gate-check` | An invoked gate and each actually executed check. Skipped checks and a disabled leak scan do not enter a check scope. |
| `review` | One reviewer invocation, attributed to its review round. |
| `merge-eligibility` | Assessment/refresh of one candidate in this dispatcher pass; not a merge. |
| `merge` | Actual PR merge or upstream integration (which may contain a gate child). Only a successful PR merge or upstream push records `merged`. Approval alone does not. |

Evidence kinds retain the common keys above and add these kind-specific keys:

| Kind | Additional keys |
|---|---|
| `handoff` | `handoff_id`: UUID string, persisted before launching a child. |
| `child_start` | `child_process`: process identity object; `handoff_id`: UUID string or null. |
| `child_exit` | `child_process`: the recorded child identity object. |
| `lock_acquired`, `lock_released` | `lock`: path string; `locks` reflects the updated recorded set. |
| `check` | `check`: string check name. |
| `timeout` | `command`: array of argument strings; `timeout_seconds`: integer. |
| `result` | Depending on the mechanism: `returncode` (integer), `command` (argument-string array or string), `timed_out` (boolean), `timeout_seconds` (integer), `check` (string), `passed` (boolean), `verdict` (`"APPROVE"` or `"REVISE"`), and/or `parsed` (boolean). These keys are present only when that evidence was obtained. |
| Ordinary `exit` with unreaped children | `evidence`: object containing `children` (process identity/state objects as below). Default `completed` becomes `unknown`; a supported exception classification is retained. |
| Reconciled `exit` | `reconciled`: true; `observer_process`: process identity object; `evidence`: the observation object described below. |

### Outcomes and uncertainty

| `outcome` | Meaning |
|---|---|
| `completed` | The entered operation returned normally; not a claim that a ticket or PR is complete. |
| `product_feedback` | Configured checks found failing code, a parsed successful reviewer requested `REVISE`, or triage requested information/human attention/proposed wontfix. |
| `project_escalation` | The existing project escalation path ran; not a broken runtime mechanism. |
| `approved`, `merged`, `refreshed` | The corresponding operation succeeded; these are distinct outcomes. |
| `not_admitted`, `not_eligible` | Admission re-read refused the ticket, or merge prerequisites did not allow merging. No skipped downstream scope is invented. |
| `mechanism_failure` | Evidence that the configured mechanism could not run, such as a missing/permission-denied executable or unavailable triage endpoint. |
| `unknown` | The cause is unsupported or uncertain, including unexplained worker/reviewer nonzero exits, unparsed verdicts, command/API errors, and timeouts. A timeout alone is not proof of runtime failure. |
| `interrupted` | A previously unterminated execution was reconciled using authoritative liveness and lock evidence. |

Reasons include `configured_check_failed`, `conflict_markers`,
`leak_scan_matches`, `APPROVE`/`REVISE`, `check_timeout`,
`triage_endpoint_unavailable`, `no_tickets`, `state_changed`, `ci_pending`,
and `no_passing_ci`; exceptions may instead provide diagnostic text. Reasons
are evidence, not a replacement for `outcome`. Existing gate exit codes,
review retry behavior, claim locks, concurrency limits, and merge prerequisites
are unchanged.

### Observation, interrupted writes, and CLI semantics

The existing dashboard snapshot (`factory dashboard --json` or
`/api/snapshot`) includes an additive top-level `executions` array. It retains
every observed execution, including concurrent stages, no-ticket runs, and local
evidence when GitHub collection fails. Each entry contains
`execution_id`, `parent_execution_id`, `root_execution_id`,
`dispatcher_run_id`, `ticket`, `attempt`, `review_round`, and `stage` with the
types above, plus:

- `state`: `"active"`, `"completed"`, `"failed"`, `"interrupted"`, or `"unknown"`.
  Only `mechanism_failure` maps to `failed`; product feedback/escalation and
  other known ordinary outcomes map to `completed`.
- `entered_at`: source `enter` timestamp; `ended_at`: source/observed exit
  timestamp or null. An exit with unknown outcome has an `ended_at` but is
  not active.
- `outcome` and `reason`: terminal values or null while unterminated.
- `events`: source lifecycle rows in sequence order.
- `evidence`: normally null on ordinary terminal exits, or the partial
  `children` object above when the scope exited before reaping its children.
  An unterminated/reconciled observation instead has an object with
  `process` (`"alive"`, `"dead"`, `"unknown"`), `children` (objects with
  `process` identity and the same `state` values), `locks` (recorded lock
  objects plus `state`: `"held"`, `"free"`, or `"unknown"`), `descendants`
  (process identities), `scan_complete` (boolean: process-scan readability),
  `descendant_absence_proven` (boolean: authoritative absence of surviving
  descendants), and `pending_handoffs` (UUID-string array).

Observation uses Linux `/proc`, boot identity, PID namespace, process start
ticks, recorded children/causal descendant context, and existing lock inodes.
A reused PID or an old artifact cannot prove activity. A positively identified
live owner or descendant is active, even if its parent ended. On the same boot,
a held lock alone prevents declaring interruption but does not identify an
active stage.
Inaccessible/incomplete process evidence, a missing/replaced lock inode, or an
unresolved launch-registration gap yields unknown when no live process can be
proven. For an execution that previously launched a process tree (including
through nested scopes), a same-boot scan cannot rule out a reparented orphan
that removed its lifecycle environment. It therefore remains unknown after
the last recorded/tagged survivor disappears; absence from the scan is not
proof that the entire tree ended. A still-live registered or tagged child
remains active. An abruptly killed scope that never launched descendants can
be reconciled when its recorded process is known dead, no pending handoff
remains, and its recorded locks are free. Interruption requires authoritative
evidence that every possible descendant ended, not merely that none was found.
A known boot-ID change proves the previous execution and its descendants ended,
even with a pending handoff or a lock held by a current-boot process. A same-boot
PID-namespace mismatch remains unknown. `scan_complete` alone never proves
descendant absence; this conservative limitation also applies to open ancestor
scopes of a launched tree.
Observation does not change scheduling or lock ownership.

The first conclusive observation appends one stable interruption exit under the
journal lock. Repeated observations reuse that record and its actual observation
time; they do not manufacture another transition. Thus dashboard observation can
write reconciliation evidence locally, but does not mutate GitHub. The existing
15-second HTTP snapshot cache remains; `?fresh=1` requests a fresh observation.
No separate lifecycle CLI or additional network probe is introduced.

The existing ticket `phase` is null unless there is one unambiguous active
leaf among its unresolved executions. Active wrappers do not hide their child
stage; an unknown child or simultaneous independent stages prevents selecting
one. When present, `phase` keeps `at` (source entry timestamp), `artifact`
(legacy field name, now the authoritative stage string), and `attempt`
(integer or null), and adds `execution_id` (UUID string). Logs/reports remain
available as evidence, never as stage truth. A ticket lock still drives the
existing in-flight/admission count, not proof of a particular executing phase.

All Factory producers serialize complete UTF-8 JSON lines under a shared file `flock`,
flush and fsync before returning, and allocate execution ordering under that
same lock. Readers accept only newline-terminated JSON objects and skip malformed
JSON, non-object rows, and unterminated tails. On the next append an unterminated
tail is invalidated with a NUL byte and newline before the new record; even a
syntactically complete but uncommitted tail is never promoted into activity.
Old unversioned rows retain their existing event names and fields without
retrofitted IDs, stages, or liveness. New legacy ticket events emitted within
an execution also carry `execution_id` (UUID string) and `dispatcher_run_id`
(UUID string or null) as causal references; they are still not lifecycle rows.
Dashboard spend counts only legacy `attempt` rows;
`learn` excludes lifecycle rows from finished-ticket selection and evidence;
`stats` still uses GitHub issue/PR comments, so lifecycle exits do not double
attempt, review, or outcome counts. `dispatch --dry-run` and
`triage --dry-run`/`--replay` record no lifecycle activity.
Dispatcher dry-run does not create claim/merge lock files or fetch remote refs.
With an upstream configured, it reports that a real pass would fetch and
evaluate upstream rather than claiming a fresh behind/ahead count.

Because the journal may contain a torn row, a tolerant local ticket query is:

```sh
python - <<'PY'
import json
from pathlib import Path
from factory.lifecycle import read_events
for row in read_events(Path(".factory/events.jsonl")):
    if row.get("ticket") == 42:
        print(json.dumps(row))
PY
```

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

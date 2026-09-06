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
| `factory dashboard` | Local ops UI: tickets by stage, authoritative in-flight phase when known, gate reports, worker logs, journal heartbeat, upstream drift, and an action list with one-click answers. `--json` prints the existing snapshot, including independent executions and local interruption reconciliation. `--host 0.0.0.0` exposes it (and its mutating `/api/act`) to your network. |
| `factory dashboard --runtime-json` | One bounded schema 1 runtime observation using only local read-only evidence; no GitHub, model probe, journal append, lock acquisition, or state creation. Partial source failures remain structured JSON. See [runtime contract](#bounded-runtime-json-schema-1). |
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
| `landing` | The existing nonblocking merge-lock request and, when acquired, upstream sync/merge pass through actual unlock. |
| `ticket` | A ticket admission/invocation, including nonblocking lock request and admission re-read. Admission refusal is not worker time. Later PR passes have separate `merge-eligibility` executions. |
| `triage`, `triage-ticket` | Whole triage invocation and each actual ticket decision. Standalone triage retains its own root identity and null dispatcher association. |
| `worker` | One worker subprocess attempt. |
| `gate`, `gate-check` | An invoked gate and each actually executed check. Skipped checks and a disabled leak scan do not enter a check scope. |
| `review` | One reviewer invocation, attributed to its review round. |
| `merge-eligibility` | Assessment/refresh of one candidate in this dispatcher pass; not a merge. |
| `merge` | Actual PR merge or upstream integration (which may contain a gate child). Only a successful PR merge or upstream push records `merged`. Approval alone does not. |
| `resource-observation`, `scheduling-observation` | Change-only local evidence records, not pipeline invocations. They have stable observation-scope execution/root IDs, no `enter`/`exit`, and null parent/dispatcher/ticket/attempt/round fields; execution occupancy excludes them. |

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
- `wait`: the current known wait object plus source `event_id` and `at`, or null. It is distinct from
  execution liveness: a live waiting process can have `state: "active"` without
  doing check/worker work. Unknown/dead/terminal executions do not retain a
  current wait; their source wait rows remain historical evidence.

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

The existing ticket `phase` is null unless there is one unambiguous active,
non-waiting leaf among its unresolved executions. Active wrappers do not hide their child
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

### F02 waits and evidenced resource ownership

F02 adds evidence to the version 1 lifecycle journal, not a second telemetry
stream, controller, resource broker, or lock. All rows retain the identities,
sequence, source timestamps, and interruption rules above. The bounded F03
runtime CLI below reads these same producer records without persisting observations.

Known waits use `kind: "wait"` with a `wait` object:

| Key | Type and meaning |
|---|---|
| `reason` | Nonempty string for a known reason; absent knowledge is represented by no current wait, never guessed from elapsed duration. |
| `mode` | `"blocking"` (an actual acquisition can block), `"retry_next_pass"` (this pass skips), `"admission"` (capacity decision), or `"eligibility"` (observed merge prerequisite). |
| `resource` | Resource descriptor below, or null for non-resource waits. |
| `details` | Object containing only decision evidence available at that point. |

`wait_end` ends a blocking wait when acquisition succeeds; a request itself is
not acquisition. A terminal scope ends its current wait without claiming the
underlying prerequisite became satisfied. Nonblocking skips, capacity decisions,
and merge eligibility remain historical decision facts after their scope exits,
not indefinitely active stages.

| Reason | Existing observation point / details |
|---|---|
| `capacity_reached` | Admission count reached `max_active`; `active` and `max_active` integers. Demand does not prove dispatcher liveness. |
| `ticket_lock_contended` | Ticket preflight found the lock held or its nonblocking acquisition lost the race; retry next pass. |
| `merge_lock_contended` | Nonblocking landing lock miss; skip this pass, retry next pass, never convert to a blocking wait. |
| `exclusive_resource` | Gate observed its exclusive lock held before the unchanged blocking acquisition. |
| `ci_pending` | Existing `pr_checks` result contained pending checks; `pr` integer. No extra CI query or polling loop. |
| `no_passing_ci` | Existing result had no passing check; `pr` integer. The cause is unknown, not an inferred CI outage. |
| `scheduled_next_pass` | Local timer explicitly active, service explicitly idle, and a future `next_at` timestamp reported by the existing systemctl seam. |

Review revision, escalation, eligibility, execution-stage occupancy, and waits
are separate facts. None of these reasons, a held resource, or elapsed time
alone creates a machinery incident. Gate outcomes and CI/human-veto prerequisites
are unchanged.

Resource events distinguish `resource_requested`, enriched `lock_acquired`, and
enriched `lock_released`. Each includes a `resource` descriptor. Only an actual
successful flock acquisition supplies confirmed holder evidence. Acquired and
released rows share an `acquisition_id`, so delayed evidence for an older holder
cannot clear a newer acquisition. Inherited F01 `locks` support liveness only:
children and dispatcher parents do not thereby become resource owners.

The gate subprocess acquires its exclusive lock once, immediately before the
first non-skipped exclusive check, and retains it through all remaining checks.
Ticket locks span the pipeline; the landing lock spans upstream sync and merge.
There are no new exclusion locks, changes to acquisition order, retry policy,
capacity accounting, check execution, or scheduling.


Resource descriptors and observations have this serialized contract:

| Descriptor key | Type and supported scope |
|---|---|
| `id` | Opaque UUID string, stable for the canonical lock pathname and supported scope; not a ticket number or dependency name. |
| `scope` | `"repository"` for ticket/merge exclusion, or `"host"` for the configured exclusive gate lock. |
| `host_id` | Opaque host identity string derived from machine identity; without machine identity, limited to the current boot. If neither authority exists, a process-local opaque fallback prevents cross-host grouping and observations remain unknown. |
| `repository` | Canonical journal-directory string for repository scope; null for host scope. Different repository journals do not imply shared ticket/merge exclusion. |
| `lock` | F01-style `path`, `device`, `inode` evidence for the canonical pathname. Missing inode/device is unknown authority. |

Host-scoped IDs permit grouping only observations of the same configured lock
on the same supported host identity. Different lock paths are not the same
GPU or dependency merely because their check names match. Canonical symlink
paths coincide; hard-link aliases and independently configured paths are not
automatically unified. Identity names a lock pathname, not every past inode
unlinked from it. A replaced inode cannot confirm an old acquisition. A single
repository's observation does not prove every factory is affected; no journal
from another repository is read to guess its holder.

All three resource operation kinds add `blocking` (boolean: acquisition mode)
and `acquisition_id` (UUID string for acquired/matched released; null on
requested or an unmatched release, which cannot clear a holder).
The F01 `lock` path remains on acquired/released rows. The common execution,
root, parent, dispatcher, ticket, attempt, review-round, and process fields
identify the actual requester/holder; request identity is never substituted for
holder identity. `wait_end.wait_event_id` names the ended wait's event UUID.

| Resource observation key | Type and meaning |
|---|---|
| `resource` | Descriptor above. |
| `state`, `ownership` | Independent string enums described above. `none` is supported only by observed free state. |
| `owner` | Null or object with all common execution identity fields, recorded `process`, `acquisition_id`, and source `acquired_at` timestamp. |
| `requests` | Array of currently live, unterminated requesters not yet acquired/released: common execution identity fields, `process`, source `event_id`, `requested_at`, and `blocking`. A pending request is not ownership. |
| `evidence` | Object: `lock_state` (`held`/`free`/`unknown`), `attribution` (`proc_locks`/`unavailable`), `holder_pids` (integer array or null). Kernel PIDs alone are not confirmed execution identity. |
| `event_id`, `at` | Stable identity and source time of the last distinct local resource observation. |
| `observed_at` | UTC time of this local evidence collection, distinct from transition time. |

Changes are persisted as `kind: "resource_observation"` in the same lifecycle
journal, carrying `resource`, `state`, `ownership`, `owner`, `requests`, and
`evidence`. Observer rows have `reconciled: true` and the observer's `process`.
Acquisition/release history remains separate from current attribution.

`dispatcher.schedule` contains `wait` (the wait object above or null),
`timer_active` and `service_active` (boolean or null), `event_id`, `at`, and
`observed_at` with the same transition-versus-collection distinction.
Change-only `kind: "scheduling_observation"` rows carry `wait`, `timer_active`,
and `service_active`; scheduled wait details include `next_at` (UTC timestamp).

The full dashboard JSON adds `resources`. Each resource observation separates
`state` (`held`, `free`, `unknown`) from `ownership` (`confirmed`, `unknown`,
`none`). A held lock is not proof of ownership. Confirmation requires both a
live recorded process identity and matching kernel lock attribution; missing
authority, external processes, old F01-only lock rows, and attribution gaps
remain unknown. A dead recorded execution is never retained as a confirmed
current holder, even if its old lock remains held.

Resource observation timestamps describe when evidence was collected. A
reconciled ownership change or free observation does not invent the exact time
an unobserved process died or released its lock. Repeated unchanged observations
reuse transition identity/time rather than producing repeated release/wait events.

`dispatcher.timer.active` and `dispatcher.service_active` now accept null when
local authority is unavailable. False means explicitly inactive/failed, not a
missing command, inaccessible service manager, or absent output. The additive
`dispatcher.schedule` records timer/service evidence and a known scheduled wait
only when confirmed; otherwise its `wait` is null. Timer interval configuration
and an empty frontier do not establish next-pass intention or deliberate pause.
Dashboard status/configuration display unknown and suppress unsupported
countdowns. Schedule observations never create a dispatcher-run identity.

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

## Bounded runtime JSON (schema 1)

`factory dashboard --runtime-json` prints one JSON object and exits. It is a
separate local read path, **not** a filtered full snapshot. `--json`, HTTP
`/api/snapshot`, and the normal dashboard retain their existing slower GitHub,
triage-probe, and writable reconciliation behavior described above.

The runtime command accepts no server options (`--host`, `--port`, `--no-open`)
and cannot be combined with `--json`. Argument errors exit 2. Fatal repository
discovery/configuration errors exit nonzero with a sanitized diagnostic on stderr
and no runtime JSON. A usable projection, including partial or wholly unavailable
runtime sources, exits 0: inspect `errors` and observation quality, not just exit
status. Missing GitHub credentials are irrelevant; this command never invokes
`gh`, remote Git operations, model probes, or network APIs.

### Consumer contract

All listed keys are required unless explicitly described as kind-specific.
Nullable values mean unknown/not applicable, never zero, stopped, or a newly
observed transition. Times are UTC ISO 8601 strings. Source times remain unchanged
on repeated reads; `generated_at` and `observed_at` are collection times, not
event freshness. Consumers must ignore additive keys and reject unsupported
schema versions rather than interpreting them as version 1.

| Top-level key | Type / meaning |
|---|---|
| `schema_version` | Integer, exactly `1`; implemented runtime contract, independent of package version. |
| `generated_at` | UTC string, generation time of this projection. |
| `repo` | Configured `owner/repository` string from the main checkout. |
| `dispatcher` | Local service/timer/admission evidence object below. |
| `executions` | Independent execution objects below; overlapping stages are retained. |
| `resources` | Current resource evidence objects below. |
| `events` | Bounded deduplicated supported lifecycle records; never synthetic poll events. |
| `history` | Explicit retained-window coverage object below. |
| `errors` | At most 32 structured partial-error objects, `{source, scope, code}` strings. No exception text, credentials, configuration dumps, or log excerpts. |

`dispatcher` has nullable booleans `service_active`, `timer_active`, and `paused`;
nullable UTC `next_at`; UTC `observed_at`; string `observation`; `capacity`;
`run_ids` (sorted dispatcher-run identity strings); and `latest_transition`
(null or `{event_id, at, execution_id, kind}` from a returned `enter`/`exit`).
Latest means the last retained observed transition in journal order, not an
artifact modification. `capacity` has `configured` (integer), `active` (integer
or null, actual held ticket admission locks), and `complete` (boolean).
Stage count is not admission count. An unavailable service query is null,
not false; inactive service evidence alone does not establish unexpected stop.
`paused` is null because the current producer has no recorded pause intention.
No scheduled intention is inferred from a configured interval.

Each execution has the eight common F01 identity fields (`dispatcher_run_id`,
`root_execution_id`, `execution_id`, `parent_execution_id`, `ticket`, `attempt`,
`review_round`, `stage`) with their types above, plus `state` (`active`,
`completed`, `failed`, `interrupted`, `unknown`), nullable `entered_at`,
`ended_at`, `outcome`, `reason`, and `wait`; `latest_event_id`, `latest_at`;
`observation` and `observed_at`. `wait` uses the F02 object plus source `event_id`
and `at`. The events live only in the top-level array, not duplicated per scope.
An entry outside the bounded window has null `entered_at` and explicit partial
coverage. A missing stage is never reconstructed from logs or artifacts.
Resource/scheduling observation scopes do not become executions.

Recorded exits retain their actual source times and ordinary outcome semantics.
A locally proven interruption without a stored exit changes only the current
execution state: no event is appended, no completion UUID is manufactured, and
`ended_at` stays null. Direct process identity and registered children can prove
liveness. The runtime path does not scan every process environment; missing
descendant evidence remains partial/unknown, not a fabricated completion.
A known boot change can still prove interruption. Unsupported older records
never establish current execution activity.

Resources retain the F02 descriptor, `state`, `ownership`, `owner`, `requests`,
and kernel `evidence` types documented above, plus `observation` and UTC
`observed_at`. `event_id` and `at` are nullable: they identify a retained,
matching persisted observation, not the latest request or this poll. A current
kernel observation without such a record has no invented transition identity
or onset. Confirmation requires matching acquisition identity, inode, kernel
holder PID, and live process identity. A request, inherited lock, replaced inode,
or external holder never becomes a confirmed owner. Released/terminal holders
are removed; source request/acquisition/release events remain in `events`.

### Bounds and partial sources

Repository discovery runs local `git --no-optional-locks rev-parse`; only when a
slug is absent does it run local `git remote get-url origin`. These do not fetch
or contact remotes. The normal main-checkout lookup and host/repository
`merge`/`host_filter` precedence are retained. Only runtime configuration fields
are interpreted: slug, nonnegative integer `dispatch.max_active`, and gate lock.
Each repository/host TOML read is capped at 256 KiB. Invalid runtime configuration
produces `factory: runtime configuration unavailable or invalid`, without echoing
the input. Unrelated worker/model/check settings are not evaluated.

Three allowlisted `systemctl --user` queries read service state, timer state,
and JSON timers. Each local command has a 0.5-second deadline, stdout strictly
below 64 KiB, discarded stderr, and at most another 0.5 seconds for reap after
kill. At most five commands run (four with an explicit slug). The D-Bus address
is forced to a local Unix socket, never an inherited TCP address. Missing tools,
unavailable units, invalid output, overflow and timeouts yield partial errors.
Only explicit `ActiveState=active` is true; transitional states remain unknown.
`next_at` requires an active timer and an explicitly returned future timestamp.
Admission scans at most 1024 directory entries and a 256 KiB kernel lock window;
unreadable/incomplete evidence returns null capacity, not a false zero.

System query errors use source `systemctl`, scope `service`, `timer`, or
`schedule`, and codes `invalid_unit`, `timeout`, `output_limit`, `command_failed`,
`command_unavailable`, `unit_unavailable`, `transitioning`, `unit_failed`,
`invalid_state`, or `invalid_schedule`. Admission errors use source `admission`,
scope `repository`, with `missing`, `unreadable`, `unsupported_file`, `byte_limit`,
`entry_limit`, `changed`, or `invalid_kernel_locks`. Errors contain fixed codes
only; raw stderr and arbitrary stored diagnostic text are never returned.

The journal reader performs one `pread` of at most **1,048,576 bytes** at the
end of the regular file, with no journal lock. It drops the first clipped line,
accepts only newline-terminated UTF-8 JSON objects of at most **16,384 bytes**
(including newline), rejects nonfinite numbers/depth over 32, and returns at most
**512 newest supported unique event identities**. Both bounds clip the beginning,
never promote an uncommitted last line. No logs or full lifetime journal scan.
Concurrent size/mtime changes mark the read partial; it is not an atomic snapshot
across files, processes, or service queries. Ordinary local filesystem reads are
assumed responsive; byte limits do not promise recovery from a kernel-stalled
filesystem.

`history` has these required fields:

| Key | Type / semantics |
|---|---|
| `source` | String, `events.jsonl`. |
| `status` | `empty` for an existing zero-byte journal; `available` for a readable nonempty journal (even with no usable records); `missing`; or `unreadable`. |
| `start_at`, `end_at` | Nullable UTC strings: minimum/maximum **returned supported** source timestamps, not file age or an inferred lifetime interval. Both null when none survive. |
| `complete` | Boolean: the present file was fully covered without detected gaps; never a promise of exhaustive lifetime history or producer instrumentation. An empty existing file is complete with a null interval. Missing/unreadable storage is incomplete. |
| `truncated` | Boolean: a byte/event bound clipped the beginning. Corruption is a gap, not necessarily truncation. |
| `gaps` | Deduplicated fixed code strings in deterministic discovery order. |
| `bytes_read`, `byte_limit`, `event_limit`, `retained_events` | Nonnegative integers; actual journal bytes read, 1048576, 512, and returned unique event count. |

Truncation makes the execution census partial: entire still-open scopes can be
outside this window. Do not interpret an empty returned execution array as proof
of no work when history is partial. Retained mid-execution scopes have unknown
state and null entry time unless the entry is actually retained. A corrupt or
unsupported suffix may hide an exit: affected open executions become unknown,
while unaffected recorded terminal facts survive. No intermediate transition,
entry time, completion, or lifetime interval is inferred.

Events are returned in retained physical journal order. Within an execution,
reduce by `sequence`; wall clocks and append order do not impose causality across
independent scopes. Exact duplicate identities are returned once, using the first
copy in the selected window. Conflicting copies of an identity, or different
identities reusing one execution sequence, keep the first copy and mark
`duplicate_conflict`; consumers must not replay the conflicting copy. Execution
identity inconsistencies and missing sequences are gaps. Executions/resources
use first-retained-appearance order (configured resource descriptors are appended
when absent); requests use execution reduction order. All are deterministic for
unchanged storage/evidence. `latest_event_id`/`latest_at` use the greatest retained
execution sequence; `dispatcher.latest_transition` uses physical order.

Event common keys/types are F01 above. Supported kinds are `enter`, `exit`,
`handoff`, `child_start`, `child_exit`, `check`, `result`, `timeout`,
`lock_acquired`, `lock_released`, `resource_requested`, `wait`, `wait_end`,
`resource_observation`, and `scheduling_observation`. Kind-specific optional
fields are `handoff_id`, `wait_event_id`, `acquisition_id` (identity strings,
nullable where F01/F02 permits); `blocking`, `parsed`, `timed_out`, `reconciled`,
`passed` (booleans); `returncode`, `timeout_seconds` (integers); `check` (bounded
string); `verdict` (`APPROVE`/`REVISE`); and the documented `child_process`,
`resource`, `lock`, and `wait`. Resource observations retain their sanitized F02
state/owner/request/evidence fields. Scheduling observations retain nullable
`timer_active`, `service_active`, and `wait`.

Supported outcomes are `completed`, `mechanism_failure`, `interrupted`,
`unknown`, `product_feedback`, `project_escalation`, `approved`, `merged`,
`refreshed`, `not_admitted`, and `not_eligible`; unknown outcome strings become
null. Only known producer reason codes (including numeric worker/review/merge/push
exit reasons) survive. Arbitrary diagnostic reasons become null, including
`wait.reason` when unsupported. Wait details retain only nonnegative integer
`active`, `max_active`, `pr`, and valid UTC `next_at` when present. Raw commands,
exception messages, arbitrary details, and unknown extra fields are omitted.
Identity/stage strings are at most 256 characters; paths at most 4096; each event
has at most 64 recorded locks. Invalid authority is a gap, not confirmed activity.

Execution `evidence` is required and nullable. When present it has `process`
(`alive`/`dead`/`unknown`), `children` (process/state objects), `locks` (F01
descriptors plus held/free/unknown state), `descendants` (empty array: no whole
process scan), `scan_complete` and `descendant_absence_proven` (booleans), and
`pending_handoffs` (identity-string array). These flags do not prove absent
descendants across missing history. Observation quality is `fresh`, `partial`,
or `unavailable`; fresh describes current evidence collection, not a recent
source event. No age-based stale threshold is invented by Factory.

Direct identity checks are cached for at most 128 PIDs, with 4096-byte `/proc`
stat reads, a 64 KiB mounts read, 128-byte boot/machine identity reads, and at
most 512 cached lock stats. Resource attribution reads `/proc/locks` once, under
1 MiB; at most 128 current holder PIDs are returned per resource. Hitting these
bounds yields unknown/partial evidence. The separate admission query has its
own smaller kernel bound above. No flock is acquired, no ownership file is
rewritten, no lock/state directory is created, and no reconciliation is persisted.
Without host identity, configured descriptors are omitted with an error rather
than assigning unrelated hosts a fabricated shared identity.

History errors use source `events.jsonl`, scope `history` or `executions`:
`missing`, `unreadable`, `not_regular`, `changed_during_read`, `byte_limit`,
`event_limit`, `row_limit`, `unterminated_tail`, `invalid_json`, `invalid_record`,
`unsupported_record`, `unsupported_version`, `unsupported_kind`,
`invalid_lifecycle`, `duplicate_conflict`, `identity_conflict`, `sequence_gap`,
`missing_enter`, `sequence_conflict`. Legacy/unversioned records are explicitly
unsupported for lifecycle coverage, not an empty valid lifecycle history.
Known legacy rows cannot hide a lifecycle exit, so they do not independently
invalidate supported open scopes.

Other source/scope pairs are `proc`/`identity` (`boot_unavailable`,
`namespace_unavailable`, `process_limit`, `process_unavailable`,
`absence_unavailable`), `proc`/`executions` (`descendants_not_scanned`),
`proc/locks`/`resources` (`locks_unavailable`, `holder_limit`),
`filesystem`/`resources` (`lock_limit`, `not_regular`, `lock_unavailable`,
`canonical_path_unavailable`), and `configuration`/`resources`
(`lock_limit`, `invalid_scope`, `host_identity_unavailable`).
Errors are deduplicated by source/scope/code; the first 32 are retained in
deterministic discovery order, lifecycle/resource errors before service errors.
Per-source quality/history flags remain authoritative even if the error cap is
reached. Resources report unavailable authority through their own quality flags;
independent service failures do not erase them.

### Observed schema 1 example

Actual guarded CLI output from a disposable repository on the development host,
with a valid empty journal and no installed matching timer/service. Only whitespace
is condensed below. Resource paths/IDs are evidence from that disposable run,
not deployment configuration. Empty history is distinct from unavailable services
and resource authority.

```json
{
  "schema_version": 1,
  "generated_at": "2026-09-05T23:15:55.985407Z",
  "repo": "example/runtime",
  "dispatcher": {
    "service_active": null, "timer_active": null, "next_at": null, "paused": null,
    "observed_at": "2026-09-05T23:15:55.985384Z", "observation": "partial",
    "capacity": {"configured": 2, "active": 0, "complete": true},
    "run_ids": [], "latest_transition": null
  },
  "executions": [],
  "resources": [
    {
      "resource": {
        "id": "a3a6cc28-abfe-5d28-b451-def8735bd090", "scope": "host",
        "host_id": "96359d9e-9be6-5980-985b-650f726a8115", "repository": null,
        "lock": {"path": "/tmp/tmp531zga_5/gpu.lock", "device": null, "inode": null}
      },
      "state": "unknown", "ownership": "unknown", "owner": null, "requests": [],
      "evidence": {"lock_state": "unknown", "attribution": "unavailable", "holder_pids": null},
      "event_id": null, "at": null,
      "observed_at": "2026-09-05T23:15:55.975966Z", "observation": "unavailable"
    },
    {
      "resource": {
        "id": "8d670505-a426-515c-bd0f-5869cb68f3e4", "scope": "repository",
        "host_id": "96359d9e-9be6-5980-985b-650f726a8115",
        "repository": "/tmp/tmp531zga_5/.factory",
        "lock": {"path": "/tmp/tmp531zga_5/.factory/locks/merge.lock", "device": null, "inode": null}
      },
      "state": "unknown", "ownership": "unknown", "owner": null, "requests": [],
      "evidence": {"lock_state": "unknown", "attribution": "unavailable", "holder_pids": null},
      "event_id": null, "at": null,
      "observed_at": "2026-09-05T23:15:55.975966Z", "observation": "unavailable"
    }
  ],
  "events": [],
  "history": {
    "source": "events.jsonl", "status": "empty", "start_at": null, "end_at": null,
    "complete": true, "truncated": false, "gaps": [], "bytes_read": 0,
    "byte_limit": 1048576, "event_limit": 512, "retained_events": 0
  },
  "errors": [
    {"source": "filesystem", "scope": "resources", "code": "lock_unavailable"},
    {"source": "systemctl", "scope": "service", "code": "unit_unavailable"},
    {"source": "systemctl", "scope": "timer", "code": "unit_unavailable"}
  ]
}
```

### Release checkpoint

Support is introduced by signed-off Factory F03 issue #28 implementation revision
`2e9678551ad5600498bc02ae26ad5e5aacc7a05e`. The package remains `0.2.0`; support is
**not** implied by that package version, F03 acceptance, or merge alone.
On older installations an unrecognized `--runtime-json` option exits nonzero;
District treats that, invalid JSON, a missing schema, or an unsupported schema
as unsupported/unknown. Factory supplies no full-snapshot fallback.

Deployment and installed-host schema verification require the separately
authorized operator checkpoint. District D02 remains held until that verification
and its District prerequisites pass. The approximately-five-second ten-factory
shared-collector measurement belongs to D02; this endpoint makes no fleet
cadence or installed-host compatibility claim.

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

# Changelog

## Unreleased

- Add optional Codebase history: stable commit-timeline maps, baseline comparisons, confidence-aware relationships, pinned source citations, and bounded background refresh through `factory[atlas]`. (#67)
- Add opt-in direction viability to `factory manage`: `needs-review` PRs before `needs-viability` issues, evidence-cited BUILD/DONT_BUILD/DEFER comments, and label-event replay protection. Only issue BUILD enters `needs-triage`; PRs remain recommendation-only, with no review or handoff mechanics.
- Fix manager prompt transport to use files, including `factory learn`; validate manager commands in doctor, bound nonzero-exit diagnostics, and count manager failures separately from escalation totals and rounds without overriding human takeover. (#62)
- Recognize District's `[defaults.engine]` snapshot metadata in `factory doctor` without loading it as pipeline configuration; keep warnings for unknown and misplaced host tables.

## 0.3.0 — 2026-09-08

Compared against 0.2.0 (`f9122cd`). Installed fleet-wide through District at `9478e9d`.

### Manager stage (inert until `[manager].command` is configured)

- `factory manage`: resolves untouched `ready-for-human` escalation packets with a closed, code-applied decision menu — `RETRY`, `REWRITE`, `SPLIT`, `ROUTE`, `HUMAN`. Malformed output is `HUMAN`. A `manage` event is recorded before any GitHub mutation; a failed mutation leaves the ticket with the human, records a lifecycle `mechanism_failure`, and is never replayed. (#13)
- `[manager]` config table: `command`, `rounds`, `review = "escalated" | "all"`; legacy string commands are parsed with `shlex`. `factory doctor` reports the manager only when configured. (#12)
- Manager notes: `.factory/manager/notes.md` read into every manager prompt, with write-back and consolidation. (#14)
- `factory learn` runs through the manager when configured and opens a `chore` PR carrying only `.factory-lessons.md`; unchanged without `[manager]`. (#16)
- `CURATE` decision, accepted only from `factory learn`: proposes `AGENTS.md` / `.omp/skills/**` / `CONTRIBUTING.md` changes as a `chore` PR; verification paths are never touched. (#18)
- `[defaults.manager]` caps (`max_active_cap`, `budget_min_cap`) for District's fleet-level manager pass. (#21)
- Structured escalation packets written by `escalate()` to `.factory/escalations/<n>.md`: reason, attempt table, gate/review evidence, kept worktree. (#11)

### Review stage

- Reviewer contract grounds every blocking finding in an acceptance criterion, a documented rule with its source, or a concrete correctness/security defect with trigger and impact. Required fixes are separated from optional suggestions; `REVISE` only while required fixes remain. Net-new abstractions beyond the brief need justification; missing justification alone does not block. A passing gate does not excuse a defect it did not detect. (#36)
- Reviewer defaults to `omp` with `anthropic/claude-fable-5-1`.

### Merge stage

- **Fix:** `refresh_pr_branch` erased a PR whose `agent/<n>` branch had no local copy — it started the branch from `main`, gated an empty tree, and force-pushed it; GitHub then auto-closed the PR. Refresh now starts from `origin/agent/<n>`, refuses to push a head with nothing ahead of `main`, and pulls such a PR from merge candidacy so it escalates once rather than starving the queue. (#49)
- **Fix:** upstream-sync PRs were squashed once upstream moved past the PR's tip, dropping the ancestry the sync exists to preserve. Merge method is now judged by the PR's merge-base with upstream, not upstream's current tip; sync PRs merge `main` in rather than rebase. (#41)

### Workers

- `[workers.<name>].when` rules route tickets by label/state; `ci-fix` and `conflict` default profiles. (#20)
- Per-ticket brief at claim time: `.factory/brief-<n>.md` from `git log -S`/grep of ticket nouns, recent PRs touching those files, the triage brief, and matching lessons — deterministic, token-capped, appended to the worker prompt. (#17)

### Runtime evidence and observability

- F01: authoritative execution lifecycle journal — `enter`/`exit`, child processes, handoffs, outcomes and reasons — per stage, in `.factory/events.jsonl`. (#26)
- F02: known waits and evidenced lock ownership; `ticket_lock_contended`, `merge_lock_contended`, `ci_pending` and similar are recorded, not inferred. (#27)
- F03: `factory dashboard --runtime-json` — bounded schema 1 runtime projection from local read-only evidence only; partial source failures stay structured. (#28)
- Bounded read-only FM evidence interface with a Pi consumer; grounded full decision briefings and contextual FM questions. (#25, #38)
- Runtime quality graded independently of history-window completeness; journal read once per full snapshot. (#43)

### Stats and dashboard

- Human-touch metrics: escalation count, resolver attribution (human/factory/unknown), minutes in `ready-for-human`, re-queues; `escalations_per_week` and `human_resolved_pct` in `--json` and the dashboard KPI row. (#10)
- Per-label pass rates in `factory stats`. (#19)
- Dashboard and site adopt the mikeroySoft design system; sage product theme.

### Install and onboarding

- `factory install` runs triage before dispatch in the unit, jitters timers, and serialises triage on the host lock; restarts only units that existed and changed.
- `factory init` writes `.github/workflows/ci.yml` when the repo has no workflow; `doctor` warns on a missing or placeholder workflow.
- Repository adopted under District; `agent-factory` renamed to `factory`.

### Known limitations

- The manager stage runs an arbitrary configured executable; configure the agent CLI in read-only/no-tools mode. Factory instructs it not to edit files but does not sandbox it.
- A stale *local* `agent/<n>` (behind the remote after another host advanced it) can still win in `ensure_worktree`; `--force-with-lease` does not protect because the refresh fetches first. Only relevant with a second dispatcher host. Tracked on #49.
- `cargo` has no minimum-release-age control; crates.io installs are unguarded by District's package-age policy.

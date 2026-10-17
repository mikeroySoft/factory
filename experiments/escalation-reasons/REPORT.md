# Escalation reasons: requirement vs eval vs environment vs model vs knowledge

Experiment for issue #163. Question: if every `ready-for-human` escalation carried a
cause tag, would routing on it beat today's generic escalation? Source journal:
`.factory/events.jsonl` of this repository plus its rotated `events.jsonl.N.gz`
segments (298 `escalate` rows, 2026-09-06 to 2026-10-17, 42 tickets). Production
routing is unchanged.

Reproduce: `python experiments/escalation-reasons/classify.py .factory/events.jsonl --table`
(the `--table` flag prints the table below; without it only the figures).

## Tags

| Tag | Definition | Hand-label rule used here |
|---|---|---|
| `requirement` | The spec is missing, contradictory, or asks for something the repo's own policy forbids. | The fix was a ticket or authorization change, not a code change: a `factory-protected-override` label, a scope amendment, an answer to a worker's question. |
| `eval` | The gate or reviewer is wrong or lacks a check. | Local gate PASS and CI red on the same head, or a reviewer finding that was itself wrong. |
| `environment` | Auth, tooling, host, flaky infra, or the pipeline's own mechanics. | Worker/manager/reviewer process could not run (auth, missing dependency, hang), a host-specific test failure, or a pipeline event the diff did not cause (rebase conflict with moved main, approval evidence lost, dispatcher re-fire loop). |
| `model` | The worker could have done the well-specified task from what it loaded and did not. | A reviewer required fix that cites a concrete defect in the diff against the spec or a repo rule the brief named, or a FIX round that did not apply the required fix. |
| `knowledge` | What the worker loaded did not contain a repo fact it needed. | The defect hinges on a repo fact outside the ticket, brief, and files the worker evidently read (`config.LABELS` provisioning, per-claim attempt numbering, which runtime codes are steady states). Recorded as a secondary tag; no attempt journals what it loaded, so it cannot be confirmed as primary. |

## Method

- Unit: one `escalate` row. Identical rows for the same ticket closer than one hour
  are folded into one (`COLLAPSE_SECONDS`): #13's rebase conflict re-fired on every
  dispatcher pass, 237 rows in 34 h. 298 rows → 62 escalations.
- `code`: only the two newest rows carry `dispatch.ESCALATION_CODES` (#241 landed
  2026-10-17). `classify.code_of` recovers the code for older rows from the prose,
  which comes from the same call sites (`CODE_FROM_REASON`).
- Evidence per row: the escalation packet `.factory/escalations/<n>.md` (gate report,
  latest review findings, handoff), the worker log named in the row, the `attempt`,
  `review`, `manage`, `merged` rows around it, and git history where the packet was
  not enough (the ruff timeline). Caveat: the packet is rewritten on every round, so
  for a ticket's earlier escalations it shows a later state; those rows cite the
  round-specific log or the journal instead.
- Scope: every escalation in the journal, not only the last 15–20 tickets, because
  the mechanical ones (`protected_paths`, `refresh_conflict`, `ci_failed`) take one
  look each and the whole set is 42 tickets.
- `gh` is not authenticated in the worker sandbox, so CI logs and issue comments were
  not read; the four `ci_failed` labels rest on the journal timestamps and the
  `.factory.toml` / `ci.yml` history (evidence column).

## Hand labels

62 escalations, 42 tickets. Primary tags: `requirement` 21, `model` 22,
`environment` 15, `eval` 4, `knowledge` 0. Two tags applied to 4 rows (#15, #134,
#232, #234; secondary in parentheses). No row fit none of the five tags.

| # | Ticket | When (UTC) | `code` | Hand tag | Classifier | Evidence |
|---:|---:|---|---|---|---|---|
| 1 | #13 | 2026-09-06T02:10 ×237 | `refresh_conflict` | environment | environment | identical `refresh_conflict` row every ~10 min for 34 h (237 rows): dispatcher re-escalated one rebase conflict on every pass; `.factory/escalations/13.md` |
| 2 | #15 | 2026-09-07T21:39 | `review_revise` | model (+knowledge) | model | `escalations/15.md` Finding 1: CLOSE path adds `wontfix-proposal`, a label `config.LABELS` never provisions |
| 3 | #54 | 2026-09-08T04:55 | `review_revise` | model | model | `escalations/54.md` Spec Required 1: `--json`, named by the issue, is rejected by `route_args` |
| 4 | #62 | 2026-09-09T00:26 | `review_revise` | model | model | `escalations/62.md` Required 1: `manager_failed` escalate rows inflate `stats.escalation_count` |
| 5 | #4 | 2026-09-10T18:43 | `approval_missing` | environment | environment | `approval_missing`: approval evidence not bound to the PR head after refresh; FIX round changed nothing (`escalations/5.md` handoff: 'not a defect in the branch') |
| 6 | #5 | 2026-09-10T18:43 | `approval_missing` | environment | environment | same `approval_missing` mechanism as #4, same minute; `escalations/5.md` handoff |
| 7 | #5 | 2026-09-10T22:05 | `review_revise` | environment | environment | `escalations/5.md` Required 1: branch no longer merges onto main because #78 landed in the same test lines (pipeline ordering, not the diff) |
| 8 | #79 | 2026-09-10T23:07 | `review_revise` | model | model | `escalations/79.md` Required 1-2: `updated_at` read from a payload that has `submitted_at`; 8×N `gh` fan-out under `_cache_lock` |
| 9 | #6 | 2026-09-11T03:33 | `pr_unpublished` | requirement | requirement | `logs/6-attempt-1.log`: ticket demands `gh pr review` bound to a head SHA, which `gh pr review` cannot do; worker asked 'May I use `gh api`' and committed nothing |
| 10 | #89 | 2026-09-18T21:08 | `review_revise` | model | model | `escalations/89.md` R1: truncated handoff presented as `complete` in `resume_context` |
| 11 | #89 | 2026-09-18T21:08 | `manager_failed` | environment | environment | `manager_failed` row 22 s after the REVISE escalation: manager process exited nonzero |
| 12 | #126 | 2026-09-29T14:53 | `gate_failed` | environment | environment | `logs/126-attempt-3.log`: 20 `test_settings` tests error because the deployed interpreter has no `tomlkit` (`ModuleNotFoundError`); no code change across attempts 2-3 |
| 13 | #126 | 2026-09-30T00:45 | `review_revise` | environment | environment | `escalations/126.md` Standards Required 1: `uv.lock` gained `exclude-newer` leaked from the worker's `UV_EXCLUDE_NEWER` |
| 14 | #131 | 2026-09-30T03:08 | `review_revise` | model | model | `escalations/131.md` Required 1: `[journal]` settings reach only `dispatch.configure`; gate/triage writers and stats readers keep defaults |
| 15 | #134 | 2026-09-30T21:36 | `gate_failed` | environment (+eval) | environment | `escalations/134.md` gate report: `HostConfigTest` fails with 'Could not find platform independent libraries'; handoff: same test fails on the parent commit |
| 16 | #156 | 2026-10-04T23:42 | `review_revise` | model | model | `escalations/156.md`: 'external PR reviews are still recorded as tickets', the round-1 blocking finding |
| 17 | #156 | 2026-10-05T00:05 | `review_revise` | environment | environment | `logs/156-attempt-5.log` (133 bytes): 'Error during droid execution: Authentication failed'; HEAD unchanged so the round-1 finding stood |
| 18 | #155 | 2026-10-05T22:03 | `review_revise` | model | model | `escalations/155.md` Required 1: two-dot `git diff base..HEAD` in `gate.py` blames the worker for files main changed |
| 19 | #166 | 2026-10-05T23:58 | `gate_failed` | requirement | requirement | `logs/166-attempt-3.log`: new `tests/test_brief.py` is protected and `tests/test_factory.py:2817` asserts the opposite of the ticket; no override label on the issue |
| 20 | #166 | 2026-10-06T09:46 | `gate_failed` | requirement | requirement | re-queue without the override label; same gate failure as the 23:58 row (`escalations/166.md` round 2) |
| 21 | #166 | 2026-10-06T13:30 | `gate_failed` | requirement | requirement | second re-queue without the override label; same gate failure (`escalations/166.md` round 3) |
| 22 | #177 | 2026-10-07T19:03 | `review_revise` | requirement | requirement | `escalations/177.md` Required 1 + handoff 'Remaining blocker': exit gate `ruff check .` needs edits to 108 diagnostics in protected `tests/**`; no override supplied |
| 23 | #177 | 2026-10-07T20:06 | `ci_failed` | requirement | eval | CI `gate` ran the new ruff step on the same head; it fails on the protected files the ticket did not authorize (`escalations/177.md`) |
| 24 | #167 | 2026-10-07T20:32 | `protected_paths` | requirement | requirement | `escalations/167.md` protected-paths failure: ticket's test edits under `tests/**` without `factory-protected-override` |
| 25 | #164 | 2026-10-07T20:41 | `protected_paths` | requirement | requirement | `escalations/164.md` attempt 1 FAIL on protected-paths; re-queued with override and passed unchanged |
| 26 | #173 | 2026-10-07T20:48 | `review_revise` | model | model | `escalations/173.md` Required 1: README lifecycle table still promises one `dispatcher` execution per idle pass |
| 27 | #162 | 2026-10-07T21:09 | `protected_paths` | requirement | requirement | `escalations/162.md` protected-paths failure: `tests/` assertions required by the ticket, no override label |
| 28 | #161 | 2026-10-07T21:20 | `protected_paths` | requirement | requirement | `escalations/161.md` protected-paths failure: ticket-mandated test edits, no override label |
| 29 | #160 | 2026-10-07T21:28 | `protected_paths` | requirement | requirement | `escalations/160.md` protected-paths failure: ticket adds `tests/test_agents_map.py`, no override label |
| 30 | #172 | 2026-10-07T21:30 | `refresh_conflict` | environment | environment | `refresh_conflict` after an APPROVE; `escalations/172.md` review has no required fixes |
| 31 | #171 | 2026-10-07T21:46 | `review_revise` | model | model | `escalations/171.md` Required 1: doctor reads the first `Environment=PATH=` line, systemd applies the last |
| 32 | #170 | 2026-10-07T22:32 | `review_revise` | requirement | requirement | `escalations/170.md` Required 1: ticket orders removal of per-test clears that are not redundant; reviewer: 'an owner decision, not a code change' |
| 33 | #164 | 2026-10-07T23:47 | `review_revise` | model | model | `escalations/164.md` Required 1: `gh run rerun --failed` consumed on an unfinished run, so the ticket escalates without a rerun |
| 34 | #188 | 2026-10-08T00:08 | `protected_paths` | requirement | requirement | `escalations/188.md` attempt 1 FAIL on protected-paths; same head passed once the override was granted |
| 35 | #188 | 2026-10-08T02:44 | `review_revise` | model | model | `escalations/188.md` Required 1: commit-status checks return `completedAt` 0001-01-01 and are treated as old |
| 36 | #168 | 2026-10-08T02:56 | `protected_paths` | requirement | requirement | `logs/168-attempt-4.log`: gate fails only on `tests/test_ticket_flow_map.py`, 'the one protected file Michael authorized on handoff 168/1' |
| 37 | #166 | 2026-10-08T03:11 | `review_revise` | model | model | `escalations/166.md` handoff: round-1 required fix, `rerank` raised on one missing candidate path and fell back to noun order |
| 38 | #125 | 2026-10-08T04:12 | `protected_paths` | requirement | requirement | `escalations/125.md` handoff 'Authorization and gate': override granted after the fact for added tests in `tests/test_briefing.py` |
| 39 | #168 | 2026-10-08T04:42 | `review_revise` | model | model | `escalations/168.md` round-3 review: 'The last review's blocking fix (the stale Atlas link) is in place' |
| 40 | #166 | 2026-10-08T04:53 | `refresh_conflict` | environment | environment | `refresh_conflict` 100 min after APPROVE; review in `escalations/166.md` has no required fixes |
| 41 | #204 | 2026-10-08T18:12 | `protected_paths` | requirement | requirement | `escalations/204.md` attempt 1 FAIL on protected-paths; manage decision HUMAN, re-queued with override |
| 42 | #125 | 2026-10-08T18:37 | `ci_failed` | eval | eval | local gate PASS at 18:33Z, CI `gate` red at 18:37Z: `ci.yml` ran ruff from b474574 (Oct 8 01:05Z) while `.factory.toml` gained the ruff check only in 60fe58e (Oct 8 21:19Z) |
| 43 | #208 | 2026-10-08T20:45 | `protected_paths` | requirement | requirement | `escalations/208.md` protected-paths failure listing the ticket's `tests/` assertions |
| 44 | #196 | 2026-10-08T21:18 | `protected_paths` | requirement | requirement | `escalations/196.md` handoff 'Protected-path scope': appended tests in `tests/test_dispatch_lifecycle.py` before the override existed |
| 45 | #204 | 2026-10-08T21:47 | `ci_failed` | eval | eval | local gate PASS at 21:08Z, CI `gate` red at 21:47Z; worktree branched before 60fe58e, so the local gate ran no ruff |
| 46 | #195 | 2026-10-08T22:20 | `refresh_conflict` | environment | environment | `refresh_conflict` after APPROVE; `escalations/195.md` review has no required fixes |
| 47 | #194 | 2026-10-08T22:42 | `ci_failed` | eval | eval | `logs/194-attempt-5.log`: FIX round 'fixed Ruff's `re.M` diagnostic'; local gate (no ruff) had passed the same head at 22:07Z |
| 48 | #212 | 2026-10-08T23:13 | `protected_paths` | requirement | requirement | `escalations/212.md` attempt 1 FAIL on protected-paths; same head passed with override at 02:37Z |
| 49 | #212 | 2026-10-09T03:02 | `ci_failed` | eval | eval | local gate PASS 02:37Z, CI `gate` red 03:02Z; same ruff-only-in-CI window as #125/#194/#204 |
| 50 | #196 | 2026-10-09T03:16 | `review_revise` | model | model | `escalations/196.md` Required 1: two #196 CHANGELOG entries after the rebase, the stale one describing pre-fix behaviour |
| 51 | #197 | 2026-10-09T05:37 | `budget` | environment | environment | `logs/197-attempt-2.log` is 11 bytes ('Working...') after 90 min; attempt 1 ended `stuck`; RETRY with no change merged 45 min later |
| 52 | #168 | 2026-10-11T02:32 | `refresh_conflict` | environment | environment | `refresh_conflict` three days after APPROVE (`escalations/168.md` round 3) |
| 53 | #224 | 2026-10-11T03:58 | `protected_paths` | requirement | requirement | `escalations/224.md` handoff 'Gate caveat': the ticket mandates the new `tests/test_mcp.py`, which `tests/**` protects |
| 54 | #227 | 2026-10-16T15:51 | `protected_paths` | requirement | requirement | `escalations/227.md` attempts: first gate FAIL on protected-paths, same log passes after override |
| 55 | #227 | 2026-10-16T18:29 | `review_revise` | model | model | `escalations/227.md` Required 1: `add_needs_triage` can label initiative records, contradicting README.md:127 |
| 56 | #234 | 2026-10-16T21:51 | `review_revise` | model (+knowledge) | model | `escalations/234.md` Required 1: re-queued attempts merged or never scored because attempt numbers restart per claim (`dispatch.py:2169`) |
| 57 | #232 | 2026-10-16T23:05 | `review_revise` | model (+knowledge) | model | `escalations/232.md` Required 1: `transitioning`/`byte_limit` runtime codes filed as bugs; they are steady states, not failures |
| 58 | #232 | 2026-10-16T23:32 | `review_revise` | model | model | `escalations/232.md`: FIX round changed unit-name matching; the round-1 required fix is still listed as open |
| 59 | #34 | 2026-10-17T00:07 | `review_revise` | model | model | `escalations/34.md` Required 1: `0/4` sample count where the run has 2 rows |
| 60 | #237 | 2026-10-17T02:51 | `review_revise` | model | model | `escalations/237.md` Required 1: baseline figure `0/2` where `adjudication.json` shows 1/2 |
| 61 | #235 | 2026-10-17T04:27 | `review_revise` | model | model | `escalations/235.md` Required 1: two contradictory #235 CHANGELOG entries after the rebase |
| 62 | #235 | 2026-10-17T04:43 | `gate_failed` | environment | model | `escalations/235.md`: FIX diff deleted one CHANGELOG line; gate fails `test_decisions...sanitized_import_path` (PYTHONHOME-sensitive) on the new District host path |

### What sits behind each tag

- `requirement` (21): 19 are `tests/**` edits the ticket demanded without the
  `factory-protected-override` label: 14 tickets via `protected_paths`, #166 three
  times via `gate_failed` (two re-queues repeated the same gap), #177 twice (REVISE,
  then CI on the same head). The manager was called on 16 of the 19 and answered
  HUMAN every time. #170 ordered the removal of test clears that were not redundant
  (REWRITE); #6 demanded a `gh pr review` bound to a SHA, which `gh` cannot do
  (REWRITE). #228 (`add_only_paths`) later removed the cause for the add-only cases
  (#125, #160, #168, #188, #196, #224 per their packets); #166 and #177 edited
  existing assertions and would still need the label.
- `model` (22): one concrete reviewer finding each; 17 reached a fresh APPROVE on
  the next round, #232 needed two, and #15, #54, #196, #235 left the loop (handled by
  hand or still open). Two recurrences: duplicate CHANGELOG entries after a rebase
  (#196, #235) and a FIX round that changed something other than the required fix
  (#232).
- `environment` (15): 6 conflicts with moved main (5 `refresh_conflict`, one of them
  #13's 237-row loop, and #5 where the reviewer found the branch no longer merged),
  2 `approval_missing` in the same minute, a manager crash (#89), a Droid
  worker with no credentials (#156), a deployed interpreter without `tomlkit`
  (#126, three gate attempts burned on it), a host-specific `HostConfigTest` (#134),
  `UV_EXCLUDE_NEWER` leaking into `uv.lock` (#126), a worker that printed only
  `Working...` for 90 min (#197), and a PYTHONHOME-sensitive test on the new District
  host path (#235).
- `eval` (4): one cause. CI ran ruff from 2026-10-08 01:05Z (b474574), the local gate
  only from 21:19Z (60fe58e); four approved heads (#125, #194, #204, #212) went red in
  CI within nine hours. #207 closed the gap.
- `knowledge`: 0 primary, 3 secondary (#15 `config.LABELS`, #232 runtime steady-state
  codes, #234 per-claim attempt numbering) = 4.8% of rows, under the 30% decision gate
  in the issue by a wide margin.

## Classifier

`classify.py` is rule-based and deterministic (`classify.classify`):

1. `code` alone decides 28 of 62 rows (`BY_CODE`): `protected_paths` → requirement;
   `ci_failed`, `pr_checks_failed` → eval; `refresh_*`, `approval_*`,
   `manager_failed`, `gh_failed`, `ci_runner`, `budget`, `worker_stuck`,
   `upstream_sync` → environment.
2. A credential, dependency or host signature in the worker log or gate report
   (`ENV_SIGNATURE`: `Authentication failed`, `ModuleNotFoundError`, `Could not find
   platform`, …) → environment.
3. `gate_failed`: `protected-paths: FAIL` in the gate report or "protected" in the
   log → requirement, else model.
4. `pr_unpublished`: the worker asked a question (`May I …`) → requirement, else
   environment.
5. `review_revise`: the required-fix text says the fix is an owner decision or needs
   authorization → requirement; names a worker-environment leak or a merge that no
   longer applies → environment; otherwise model.

Agreement with the hand labels (primary tag): **60/62 = 96.8%**. Counting a
secondary tag as a hit changes nothing (the classifier never emits `knowledge`).
The two misses:

- #177 `ci_failed` (row 23): hand `requirement`, classifier `eval`. CI's ruff step
  failed on the 108 diagnostics in protected tests the ticket never authorized; the
  code says CI disagreed with the gate, the cause was the ticket.
- #235 `gate_failed` after FIX (row 62): hand `environment`, classifier `model`. The
  failing test output carries no host signature; only the FIX diff (one CHANGELOG
  line) and the moved host path show it was not the worker's change.

Caveat: the rules were written after reading the same 62 rows they are scored on.
96.8% is in-sample agreement, not a forecast; the honest expectation for new rows is
"the 28 `BY_CODE` rows are free, the text rules will need maintenance". Every
`review_revise` row that is not a requirement or environment case defaults to
`model`, which is the right default here (22 of 27) but is a default, not a
detection.

## Would routing on the tag have saved rounds?

Route per the issue: requirement → owner/`needs-info`; environment → operator issue;
eval → gate/reviewer ticket; model → today's REVISE/FIX loop.

| Tag | Rows | What routing would have skipped | Saved |
|---|---:|---|---|
| requirement | 21 | The manager call (16 HUMAN verdicts, 2 REWRITE, 0 other) and the blind re-queues of #166 (rounds 2–3: 2 escalations, 6 gate attempts) and #177 (second escalation, same cause). The human action, applying the override label, is unchanged. | 16 manager calls, 3 escalations, 6 gate attempts |
| environment | 15 | #126 round 1: attempts 2–3 re-ran a gate that could not pass without `tomlkit` (2 attempts, 1 manager call). #156 round 2: a FIX round dispatched to a worker with no credentials. #13: a tag does not stop a re-fire loop; a "same reason already escalated" guard does (the one `ci_runner` has). Rebase conflicts and lost approval evidence need the same human either way. | 2 gate attempts, 1 FIX round, 1 manager call |
| eval | 4 | Four escalations and one FIX round (#194; the other three got no manager call because the merge stage had already withdrawn approval) for one gate/CI drift a human fixed once (#207). Routing to one gate ticket collapses them. | 3 escalations, 1 FIX round |
| model | 22 | Nothing; the FIX loop is the right route and resolved 17 of 22 in one round. | 0 |

Total over six weeks: 6 escalations, 17 manager calls, 8 gate attempts and 2 FIX
rounds, out of 62 escalations, 40 manager calls and 193 attempts. The saving is real
but concentrated: every row behind it is decided by `code` alone (`protected_paths`,
`ci_failed`, `gate_failed` plus the "protected" log line) except #126 round 1 and
#156 round 2, which needed a log signature. The tag adds nothing over the code there.

## Recommendation

**Adopt the grouping, drop the classifier and the second brain.**

- Adopt: the tag as a fixed `code → tag` map (rule 1 above) reported next to
  `escalations_by_code` in `factory stats`, and two routing rules on codes, not on
  text: `protected_paths` skips the manager and goes straight to the owner with the
  override request (16/16 manager calls on those rows were HUMAN), and a second `ci_failed` within
  a day files one gate ticket instead of another escalation. Both are code-driven and
  need no new journal field. This is a proposal; nothing here changes routing.
- Drop: text-signature classification in production. 34 rows needed packet or log
  text; the signatures are regexes over one repository's reviewer prose and would
  silently rot. Keep `classify.py` as the scoring harness for #140 (the automated
  classifier), which the issue says is scored against these labels.
- Drop: the `knowledge` tag and the second-brain follow-up (routed positions, routing
  index, `knowledge` promotion kind). 0 primary and 3 secondary `knowledge` labels out
  of 62 (4.8%) is far under the 30% gate, and the three cases were each fixed by the
  reviewer's citation in the next round. The `kb_loaded` attempt field was not added:
  it only matters if `knowledge` is worth tracking, and it is a production
  `dispatch.py` change the exit gate excludes.
- The one structural lesson is not about tags: the biggest class (`requirement`,
  15 rows) was already removed by #228, and the `eval` cluster by #207. Both were
  visible as a repeated `code`, which is the signal `stats` now reports.

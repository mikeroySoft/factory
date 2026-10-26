## Standards

No `AGENTS.md`/`CONTRIBUTING.md` in the snapshot; the documented surfaces are `README.md` and the `factory init` template, both updated by the diff (`review.diff:24-32`, `:40-41`, `:168-199`). Nothing to flag.

## Spec (issue #62 + agent brief)

| Criterion | Status | Evidence |
|---|---|---|
| `manager_cmd(prompt_path, cwd)` expands `{prompt}` with a file path, worker shape | Met | `review.diff:56-61`; prompt written to `<factory>/manager-prompt-<n>.md` at `:114-115` |
| Reject bare `{prompt}` for `omp` with `ConfigError` naming `@{prompt}`; other programs untouched | Met | `review.diff:59-60` |
| HUMAN body `Manager command exited N (argv: …unexpanded…)` + last 5 stderr lines | Met | `review.diff:119-122` |
| Record `escalate` event with `reason: manager_failed` | Met | `review.diff:124-125` |
| `factory doctor` row PASS/FAIL/WARN | Met | `review.diff:145-157` |
| README + template: `{prompt}` = file for workers and manager, text for reviewer; default manager shape `@{prompt}` | Met | `review.diff:24-27`, `:41`, `:197-199` |
| Exit-gate tests (argv `@<path>` readable; ConfigError; exit-1 body capped at 5 lines; doctor WARN/FAIL) | Met | `review.diff:211-226`, `:244-270`, `:299-318` |

### Required fixes

1. **`manager_failed` events inflate the human-touch escalation KPIs.** `review.diff:124-125` emits a second `escalate` event for the same ticket on every nonzero manager exit. `stats.py:167-171` (snapshot, unchanged by the diff) counts every `escalate` event: `escalation_count = max(len(starts), len(escalated))` and `escalation_times` feeds `escalations_per_week` (`stats.py:185-188`). Trigger: one real escalation (1 `ready-for-human` label start) followed by one failing manager run → `escalated=2 > starts=1`, so `escalation_count` becomes 2 and `escalations_per_week` gains one phantom escalation; the ticket was never re-escalated to a human. The diff already recognised that `manager_failed` must be excluded from `escalate` consumers for round arithmetic (`review.diff:74-75`, `dispatch.py`) but left the stats consumer. Concrete correctness defect in a documented metric (README:87 "escalation count … minutes in `ready-for-human`"). Minimal fix: same `reason != "manager_failed"` filter at `stats.py:167` (one line), plus the `stats` assertion the issue implies ("so `factory stats` can count it").

### Optional suggestions

- `review.diff:120-122`: detail is stderr-only. The original failure in the issue was captured via `proc.stderr or proc.stdout`; a CLI that prints its error on stdout now yields a body with no detail. Fall back to a stdout tail when stderr is empty.
- `review.diff:124-125` + snapshot `manage.py:237,281`: after a failure, the latest `escalate` event is the `manager_failed` one with a later `at`. If a human commented while the failing manager ran, pass N skips at `manage.py:281` without recording a `manage` event; pass N+1 loads the `manager_failed` event, `human_activity` compares against its later timestamp, misses the comment, and re-runs the manager once. Narrow trigger; one extra run and comment. Recording the `manage` event before the `human_activity` check, or filtering `manager_failed` at `manage.py:237`, closes it.
- `review.diff:97-101`: `ConfigError` from `manager_cmd` is uncaught in `factory learn` (traceback). Consistent with how `config.load()` errors already surface (no handler in `cli.py:30`), so not blocking.
- No `CHANGELOG.md` entry; the file records fixes per issue (`CHANGELOG.md:24-25`) but no documented rule requires one.

## Plan drift

Plan status not recorded, so the only baseline is the issue's **Touches** list (`config.py`, `manage.py`, `doctor.py`→`onboard.py`, template, README, tests).

- Files changed beyond that list: `factory/dispatch.py` (`review.diff:65-77`, round arithmetic; approved in the round-1 handoff comment on the issue) and `factory/learn.py` (`review.diff:79-103`, required caller of the changed `manager_cmd` signature). Neither exposes a defect; the `dispatch.py` change is what points at the unfixed `stats.py` consumer above.
- Planned proof missing from the gate report: the issue's manual exit-gate item ("re-run `factory manage` on District #26's kept packet … get a real DECISION line"). The gate report covers only conflict-markers/test/leak-scan; the handoff says this revision did not repeat the replay and cites a prior attempt's `.factory/district-26-replay.log`, which is not in the evidence here. Not blocking on its own (tests `review.diff:278-297` exercise the `@{prompt}` path with a 30k-word prompt).

VERDICT: REVISE

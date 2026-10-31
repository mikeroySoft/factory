**REVISE.** One important defect: if the manager command fails, a human who took over the ticket while it ran can be overridden on the next pass. Everything else is a nit or plan drift.

**Important — human takeover is lost after a manager failure** (`factory/manage.py:272`)
- **Cause:** the new `escalate`/`manager_failed` event is recorded before the human-activity check at `manage.py:281`.
- **Trigger:** a human comments, relabels or assigns the issue while the manager command is running, and the command then exits nonzero.
- **First pass:** `human_activity(n, original_escalation)` returns True, so the pass continues and records no `manage` event. The `manager_failed` row stays in the log.
- **Next pass:** `manage.py:237` picks that row as the latest escalation. The same round has no `manage` event, so the ticket qualifies again. `human_activity` only looks at timeline items at or after the new event's `at` (`manage.py:136`), so the earlier human action is not seen.
- **Impact:** the manager runs again. A RETRY, REWRITE or ROUTE result then comments, removes `ready-for-human` and adds `ready-for-agent` on a ticket a human had taken over. That breaks the guard the code states at `manage.py:280`.
- **Fix, either one is enough:**
  - Record the `manager_failed` event only after the human-activity check, next to the `manage` record at `manage.py:286`.
  - Or skip `reason == "manager_failed"` rows when choosing the escalation at `manage.py:237`, the same way `dispatch.py:376` already skips them.

**Spec coverage.** The rest of issue #62 is met:
- `manager_cmd(prompt_path, cwd)` now gets a file path, written to `.factory/manager-prompt-<n>.md`.
- An `omp` command with a bare `{prompt}` raises `ConfigError` naming `@{prompt}`.
- `factory doctor` has a `manager command` row with WARN, FAIL and PASS cases.
- A nonzero exit posts `Manager command exited N (argv: …)` with the last 5 stderr lines.
- README, the init template and the required tests are updated.

**Plan drift.** No plan was recorded, so these are compared against the issue's "Touches" list:
- `factory/learn.py` is not named. It is needed because the `manager_cmd` signature changed.
- `factory/dispatch.py` is not named. It came from review round 1, per the handoff.
- The doctor row lives in `factory/onboard.py`; the issue names `factory/doctor.py`, which does not exist.
- `DEFAULT_MANAGER` does not exist in the code, so nothing was changed for it. The README and template examples now use `@{prompt}` instead.
- The gate report has no evidence for the manual District #26 replay.

```json
{"verdict":"REVISE","findings":[{"severity":"important","pass":"bug","path":"factory/manage.py","line":272,"summary":"The manager_failed escalate event is recorded before the human_activity check at manage.py:281. If a human acts during the manager run and the command then fails, the pass continues with no manage event. On the next pass manage.py:237 picks the manager_failed row as the latest escalation, and human_activity ignores the earlier human action because it only reads timeline items at or after that row's time. The manager runs again and can comment or relabel the ticket to ready-for-agent despite the human takeover, breaking the guard stated at manage.py:280. Fix: record the event after the human check, next to the manage record, or skip manager_failed rows when choosing the escalation, as dispatch.py:376 already does."},{"severity":"nit","pass":"bug","path":"factory/manage.py","line":272,"summary":"stats.human_touch (stats.py:167-170) counts every 'escalate' event, so each manager failure adds one to the ticket's escalation_count and escalation_times even though no new ready-for-human escalation happened. The issue asks for manager failures to be countable, not for them to inflate the escalation metrics. Exclude reason=manager_failed there, as dispatch.py:376 does."},{"severity":"nit","pass":"spec","path":"factory/templates/factory.toml","line":44,"summary":"Unrequested change to the ci-fix and conflict worker examples (lines 44 and 48). The old inline commands worked with omp because the path was embedded in prompt text. The role-specific instructions they carried (flake-evidence rule, conflict-preservation rule) are dropped, and `when` text only reaches the manager menu, not the worker prompt."},{"severity":"nit","pass":"spec","path":"factory/manage.py","line":263,"summary":"Plan drift: the issue's manual exit gate (re-run factory manage on District #26's kept packet and get a real DECISION line) has no evidence in the gate report. The handoff cites only a replay from a previous attempt."},{"severity":"nit","pass":"spec","path":"factory/learn.py","line":64,"summary":"Plan drift: learn.py and dispatch.py are not in the issue's Touches list. learn.py is a required caller migration after the manager_cmd signature change. The dispatch.py round-arithmetic change came from review round 1. The doctor row is in onboard.py, not the non-existent factory/doctor.py. None of these alone exposes a defect."}]}
```
VERDICT: REVISE

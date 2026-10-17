## Standards

Sources checked: README.md (event contract §"Execution event contract", lines 371-375), `factory/templates/factory.toml`, `.github/ISSUE_TEMPLATE/agent_task.md`. No `AGENTS.md`/`CONTRIBUTING.md`/`docs/` standards file exists in this repo. All six commits carry `Signed-off-by` trailers; CHANGELOG has an Unreleased entry.

### Required

**R1. Manager nonzero exit mislabels the `manage` execution's lifecycle outcome as `project_escalation`.**
- `factory/manage.py:273-274` records `escalate` with `reason="manager_failed"` inside the `manage` lifecycle scope (`manage.py:225`).
- `factory/dispatch.py:245-248` (unchanged) sets `execution.outcome = "project_escalation"` and `execution.reason = "manager_failed"` for every `escalate` event recorded under a live execution.
- Documented rule, README.md:372: `project_escalation` = "The existing project escalation path ran; **not a broken runtime mechanism**." README.md:375: `mechanism_failure` = "Evidence that the configured mechanism could not run."
- Trigger: any manager command exiting nonzero (the exact #62 scenario). Impact: the `manage` execution's lifecycle `exit` row now reports `project_escalation` where before the diff it reported `completed`; `manager_failed` is not in `runtime_events.REASONS` (`factory/runtime_events.py:34-44`), so projections surface outcome `project_escalation` with reason `null` — a broken manager is classified as a healthy escalation. The sibling failure path at `manage.py:293-294` already uses `mechanism_failure`.
- Fix (one line, no new abstraction): after `dispatch.record(... reason="manager_failed")`, set `execution.outcome = "mechanism_failure"; execution.reason = "manager_failed"` (`execution` is in scope from `manage.py:225`; `dry_run` has already `continue`d at `manage.py:249-251`). Optionally add `"manager_failed"` to `runtime_events.REASONS` so the reason survives projection.

### Optional

- `factory/onboard.py:234` duplicates the omp bare-`{prompt}` rule from `factory/config.py:149` (Duplicated Code, judgement call). Two sites, one predicate; fine to leave.
- `factory/manage.py:279-280`: the `ConfigError` path leaves the execution outcome `completed` while posting "Manager command failed". Same mislabel class as R1 but pre-existing shape (OSError path behaved identically before the diff); not blocking.

## Spec

Issue #62 "After this change" + agent brief (comment 1). Handoff comment (comment 2) added only the `ConfigError` containment and round-arithmetic exclusion; no scope reductions approved.

| Criterion | Status | Evidence |
|---|---|---|
| `manager_cmd(prompt_path, cwd)` expands `{prompt}` with a file path; prompt lands at `<factory>/manager-prompt-<n>.md` | Met | `config.py:146-151`; `manage.py:264-266`; test asserts `.factory/manager-prompt-7.md` content at `tests/test_factory.py:752`. Deviation: the write happens in the caller, not inside `manager_cmd` (which has no `n`). Observable behavior matches; non-blocking. |
| Bare `{prompt}` for `omp` → `ConfigError` naming `@{prompt}`; other programs not second-guessed | Met | `config.py:149-150`; `tests/test_factory.py:164-165` |
| Default manager command becomes worker shape | Met | `templates/factory.toml:63-65`, `README.md:219-221`. No `DEFAULT_MANAGER` exists in `config.py` (`manager: list[str] \| None = None`, `config.py:105`), so only docs/template change. |
| README + init template: `{prompt}` = file for workers and manager, text for reviewer | Met | `README.md:37-38, 97-101`; `templates/factory.toml:23, 51, 63-64` |
| `doctor` `manager command` row: PASS / FAIL (missing placeholder or executable) / WARN (unset) | Met | `onboard.py:226-238`; `report(None, …)` → WARN per `onboard.py:168`; cases at `tests/test_factory.py:411-420` |
| Nonzero exit posts `Manager command exited N (argv: …unexpanded…)` + last 5 stderr lines | Met | `manage.py:268-271`; `tests/test_factory.py:745-749` asserts exactly `error-15..19` |
| Records `escalate` event with `reason: manager_failed`; `factory stats` counts it | Met (but see R1 for side effect) | `manage.py:273-274`; `stats.py:167-174, 303`; `README.md:144-146` |
| Exit-gate tests (`@<path>` readable, ConfigError, ≤5 stderr lines, doctor WARN/FAIL) | Met | `tests/test_factory.py:150-165, 410-436, 753-783` |
| Out of scope (decision semantics, packet format, model) untouched | Held | No changes to `parse`/`MENU`/`escalation_packet` body |

Scope additions beyond the brief, all from approved handoff: `manager_failed` excluded from round arithmetic (`dispatch.py:375-378`, `manage.py:237-238`), `ConfigError` caught per ticket (`manage.py:279`), `learn.py` moved to file transport (`learn.py:64-68`) — the last is required for `factory learn` not to break under the new `manager_cmd` signature. No unrequested abstractions introduced.

## Changed signatures / defaults — consumers outside the diff

- `Config.manager_cmd(prompt_path: Path, cwd)` — callers: `factory/manage.py:266`, `factory/learn.py:68` (both in diff, both pass `Path`). No others (`grep manager_cmd`). Holds.
- `stats.human_touch()` return gains `manager_failures`; `escalation_count`/`escalation_times` keys unchanged — consumers `factory/dashboard.py:635, 789`, `factory/stats.py:281, 346`, `human_touch_metrics` at `stats.py:190`. Hold.
- `dispatch.escalation_packet` round semantics — callers `dispatch.py:386-389, 590-593` unchanged; `manage.py:241-243` reads `round` from the filtered escalation. Holds.
- `dispatch.record("escalate", reason="manager_failed")` — consumer `dispatch.py:245-248` → **R1**. `learn.py:39` (`finished` detection) unaffected: a real `escalate` already precedes any `manager_failed` row.
- `doctor` row label `manager: <exe>` → `manager command` — only consumer was the removed test (`tests/test_factory.py:410` old). Holds.
- Template `[manager].command` default now `@{prompt}` — host configs with bare `{prompt}` for `omp` are rejected by both `manager_cmd` and `doctor` with the fix named; non-omp programs unaffected.

VERDICT: REVISE

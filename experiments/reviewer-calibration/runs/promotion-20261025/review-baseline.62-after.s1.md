## Standards

No `AGENTS.md`/`CONTRIBUTING.md` in the snapshot; the documented contract that applies is `README.md` §"Execution event contract" (outcome table, README:366-377) and §"Configuration".

**Required**

1. **Nonzero manager exit is classified as `project_escalation` on the `manage` execution** — `factory/manage.py:273-274` records `dispatch.record("escalate", …, reason="manager_failed")` inside the `lifecycle.scope(…, "manage")` opened at `factory/manage.py:225`. `dispatch.record` (`factory/dispatch.py:245-248`, unchanged) sets `execution.outcome = "project_escalation"` and `execution.reason = "manager_failed"` for every `escalate` row. README:372 defines `project_escalation` as "the existing project escalation path ran; **not a broken runtime mechanism**", and README:376 puts "command/API errors" and unexplained nonzero exits under `unknown` (README:375 `mechanism_failure` for a mechanism that could not run). Trigger: any manager command exiting nonzero (the exact ENAMETOOLONG case in the issue). Impact: the `manage` exit row in `.factory/events.jsonl` and `factory dashboard --runtime-json` report a broken manager command as a healthy escalation outcome; `manager_failed` is also not in `runtime_events.REASONS` (`factory/runtime_events.py:34-44`), so the bounded reader nulls the reason, leaving `project_escalation` with no evidence. Minimal fix: after the `record` call, set `execution.outcome = "unknown"` (or `"mechanism_failure"`) and `execution.reason = f"manager_exit:{proc.returncode}"`, matching `run_worker` (`factory/dispatch.py:258-259`). The issue's requirement to record the `escalate`/`manager_failed` row for `factory stats` is unaffected.

**Optional**

- Duplicated rule: the "omp needs `@{prompt}`" check exists twice — `factory/config.py:149-150` and `factory/onboard.py:234-235`. A future change to one (e.g. accepting `-f {prompt}`) silently diverges doctor from runtime. Could expose one predicate in `config.py`; not blocking.
- `factory/manage.py:279-280`: the new `ConfigError` branch leaves the manage execution at default `completed` outcome, though README:375 describes a mechanism that could not run. Same gap already existed for `OSError`; noting only.

## Spec (issue #62 + agent brief)

| Criterion | Status | Evidence |
|---|---|---|
| `manager_cmd(prompt_path, cwd)` expands `{prompt}` with a file path; prompt written to `<factory>/manager-prompt-<n>.md` | Met | `factory/config.py:146-151`, `factory/manage.py:264-266` |
| Bare `{prompt}` for argv[0]==`omp` → `ConfigError` naming `@{prompt}`; other programs not second-guessed | Met | `factory/config.py:149-150` (`Path(argv[0]).name == "omp"`) |
| Default manager command = worker shape; README §Configuration + init template document file-for-worker/manager, text-for-reviewer | Met | No `DEFAULT_MANAGER` constant exists in base (`manager: None`); docs updated `README.md:97-101,219-220`, `factory/templates/factory.toml:25,51,63-65` |
| `factory doctor` "manager command" row: PASS / FAIL naming missing placeholder or executable / WARN when unset | Met | `factory/onboard.py:226-238`; `report(None, …)` → WARN per `onboard.py:168` |
| Nonzero exit posts `Manager command exited N (argv: <unexpanded>)` + last 5 stderr lines | Met | `factory/manage.py:268-271`; test asserts exactly lines 15-19 (`tests/test_factory.py:771`) |
| Records `escalate` event with `reason: manager_failed`; `factory stats` can count it | Met (see Required #1 for the outcome side effect) | `factory/manage.py:273-274`, `factory/stats.py:173-174`, `stats.py:303` |
| Exit-gate tests: `@<path>` readable with prompt text; ConfigError on bare `{prompt}`; HUMAN body prefix + ≤5 stderr lines; doctor WARN/FAIL | Met | `tests/test_factory.py:150-165`, `410-436`, `730-781` |
| Round-1 reviewer required fixes (ConfigError contained per ticket; `manager_failed` excluded from round arithmetic) | Met | `factory/manage.py:237-238,279`, `factory/dispatch.py:375-378`, tests `tests/test_factory.py:790-802` |
| Out of scope untouched (decision semantics, packet format, model) | Met | no changes to `parse`/`MENU`/packet |

Scope creep: none material. `manager_failures` column in the stats table (`factory/stats.py:303`) and `human_touch` key go slightly beyond "stats can count it" but are the direct realization of that criterion.

## Plan drift

Plan status not recorded; compared against the issue's "Touches" list.

Files changed the issue did not name:
- `CHANGELOG.md` — repo keeps an `## Unreleased` log; expected.
- `factory/dispatch.py:375-378` — approved round-1 reviewer required fix (issue comment 2026-09-09).
- `factory/learn.py:64-68` — only other `manager_cmd` caller; required by the signature change (previously inline text).
- `factory/stats.py` — counting `manager_failed` per the acceptance text.
- Doctor row landed in `factory/onboard.py` rather than `factory/doctor.py`; no `doctor.py` exists in the snapshot — correct placement.

Planned proof missing from the gate report:
- Manual replay of District #26's kept packet with the host's current command (exit-gate item). Gate report lists only conflict-markers/test/leak-scan; the round-1 handoff claims a prior attempt produced `DECISION: FIX` (`.factory/district-26-replay.log`), unverified here. Does not expose a defect by itself; the file-transport tests (`tests/test_factory.py:730-749`, 30 000-token prompt) cover the ENAMETOOLONG path.

None of the drift exposes a required defect; the only required fix is Standards #1.

VERDICT: REVISE

The instruction text lived only in those two template commands; nothing else carries it to the worker. Findings follow.

## Standards

No `AGENTS.md`/`CONTRIBUTING.md` in the snapshot; `docs/` holds plans, not conventions. Convention sources are README §Configuration, the `factory init` template, and `skills/factory/SKILL.md`. No documented-rule breach found. Changelog has no "Unreleased" section and no documented rule requiring per-PR entries; stale historical note at `CHANGELOG.md:10` ("doctor reports the manager only when configured") is a release note, not a live rule.

## Spec (issue #62 + agent brief)

| Criterion | Evidence | Status |
|---|---|---|
| `manager_cmd(prompt_path, cwd)` expands `{prompt}` with a file path; prompt written to `<factory>/manager-prompt-<n>.md` | `factory/config.py:56-61`, `factory/manage.py:114-116` | ✓ (file written by caller, not inside `manager_cmd`; equivalent) |
| Bare `{prompt}` for `omp` → `ConfigError` naming `@{prompt}`; other programs untouched | `factory/config.py:59-60` | ✓ |
| Nonzero exit → `Manager command exited N (argv: …unexpanded…)` + last 5 stderr lines; `escalate` event `reason: manager_failed` | `factory/manage.py:119-125` | ✓ |
| `doctor` row `manager command`: PASS / FAIL naming placeholder or executable / WARN when unset | `factory/onboard.py:145-157` | ✓ |
| README + template document file for workers and manager, text for reviewer; default manager command in worker shape | `README.md:24-27,40-41`, `factory/templates/factory.toml:197-199` | ✓ |
| Exit-gate tests (argv `@<path>` readable; ConfigError; HUMAN body ≤5 stderr lines; doctor WARN/FAIL) | `tests/test_factory.py:211-226, 244-270, 299-331` | ✓ |
| `learn` caller migrated to the new signature | `factory/learn.py:97-101` | ✓ |

### Required fixes

1. **Unrequested, lossy change to the `ci-fix`/`conflict` template profiles** — `factory/templates/factory.toml:177-178, 182-183`. The original commands embedded `{prompt}` as a path inside instruction text (`"Read the ticket prompt at {prompt}. … declare a flake … Do not claim a flake without evidence."`), which already satisfies "{prompt} = file for workers"; they were never bare `{prompt}`. The diff replaces them with `@{prompt}`, deleting the only place those instructions reach the worker (`grep flake` in `snapshot/factory` finds nothing else that passes them; `worker_when` is only read by `manage.py`). Trigger: an operator enables `[workers.ci-fix]` from the template, as `README.md:119-120` invites. Impact: the FIX-routed worker runs with the default-worker prompt and no flake-evidence/no-semantic-change instructions. No acceptance criterion in #62 asks for this; the issue's scope for the template is documenting the `{prompt}` contract. Revert those two lines.

### Optional suggestions

- `factory/templates/factory.toml:169` and `README.md:27` say bare `{prompt}` "is rejected" in the worker paragraph; only `manager_cmd` rejects it (`config.py:59`), `Config.worker` (`config.py:138-141`) does not. Scope the wording to the manager.
- `factory/manage.py:124-125` records an `escalate` event; `stats.human_touch` (`stats.py:167,170`) counts every `escalate` event, so a manager crash now raises `escalation_count`/`escalations_per_week` for that ticket. The issue asked for the event "so `factory stats` can count it", so this may be intended; if not, filter `reason == "manager_failed"` at `stats.py:167`.
- `dispatch.record("escalate", …)` inside the `manage` lifecycle scope sets the manage execution's outcome to `project_escalation` (`dispatch.py:245-248`). Consider whether `mechanism_failure` is the intended lifecycle outcome for a crashed manager command.

## Plan drift

Plan status: not recorded, so no plan file list to diff against. Against the issue's **Touches**: `factory/dispatch.py` and `factory/learn.py` are not named. `learn.py` is a required caller update (signature change). `dispatch.py:73-75` (exclude `manager_failed` from round arithmetic) is needed so the new event does not consume `manager_rounds`; the handoff comment says a round-1 reviewer required it. `factory/doctor.py` named in the issue does not exist; the row lives in `onboard.py:doctor`. Planned proof missing from the gate report: the manual District #26 kept-packet replay — the handoff claims the prior attempt did it (`DECISION: FIX`, `.factory/district-26-replay.log`) and this revision did not repeat it. Neither drift item exposes a defect on its own; the required fix above is independent of drift.

VERDICT: REVISE

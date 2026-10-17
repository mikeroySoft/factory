## Standards

No `AGENTS.md`/`CONTRIBUTING.md` in the repo; standards sources are `README.md`, `factory/templates/factory.toml`, `docs/manager-plan.md`, `skills/factory/SKILL.md`. No documented-rule violations found.

Smell checks (judgement calls, none blocking):
- **Duplicated Code**: the omp bare-`{prompt}` test is written twice — `factory/config.py:149` and `factory/onboard.py:234` — with different messages. Two call sites, same predicate. Optional: doctor could call `cfg.manager_cmd(Path("x"), Path("."))` and catch `ConfigError` to keep one source of truth; the duplication is small enough that leaving it is defensible.
- **Speculative Generality**: none. No new abstractions beyond the brief.

Docs consistency: `docs/manager-plan.md:242` still describes the pre-#62 doctor contract ("prints a `manager:` row when configured and nothing when not"). It is a dated plan document, not a standard; the issue supersedes it. Optional to annotate.

## Spec

Acceptance criteria vs. diff:

| Criterion | Evidence | Status |
|---|---|---|
| `manager_cmd(prompt_path, cwd)` expands `{prompt}` with a path; prompt written to `<factory>/manager-prompt-<n>.md` | `factory/config.py:146-151`; `factory/manage.py:264-266` | Met. Brief's "manager_cmd writes the prompt" is inconsistent with its own `(prompt_path, cwd)` signature; caller-writes/method-expands is the only reading that satisfies both, and it matches `Config.worker` (`config.py:138-141`). |
| Default manager command becomes worker shape | No `DEFAULT_MANAGER` constant exists (`config.py:105` defaults to `None` = disabled); the only "default" is the template example, updated at `factory/templates/factory.toml:62-64` and `README.md:219-220` | Met. |
| Reject bare `{prompt}` for omp with `ConfigError` naming `@{prompt}`; don't second-guess other programs | `factory/config.py:149-150` (exact-element match on argv[0] basename `omp` only) | Met. |
| Nonzero exit posts `Manager command exited N (argv: …unexpanded…)` + last 5 stderr lines | `factory/manage.py:268-271` (`json.dumps(cfg.manager)` is the template, not the expansion) | Met. |
| Records `escalate` event with `reason: manager_failed` | `factory/manage.py:273-274` | Met. |
| `factory stats` can count it | `factory/stats.py:173-174`, `stats.py:303` | Met. |
| Doctor row: PASS / FAIL naming placeholder or executable / WARN when unset | `factory/onboard.py:226-238` | Met. Spec says `[dispatch.manager]`; actual table is `[manager]` (`config.py:308`), so the WARN text at `onboard.py:227` is the correct one. |
| README + template document file-for-workers-and-manager, text-for-reviewer | `README.md:36-38`, `README.md:97-105`, `factory/templates/factory.toml:25`, `:51`, `:63-64` | Met. |
| Exit-gate tests: `@<path>` readable content; ConfigError on bare `{prompt}`; HUMAN body starts `Manager command exited 1`, ≤5 stderr lines; doctor WARN/FAIL | `tests/test_factory.py:150-165`, `:410-436`, `:751-787` | Present. Gate reports `test: PASS`. |
| Round-1 approved fixes: catch `ConfigError` per ticket; exclude `manager_failed` from round arithmetic | `factory/manage.py:279`; `factory/dispatch.py:376-377`; `factory/manage.py:237-238` | Met. |

Scope creep: none. Human-takeover anchor preservation (`manage.py:237-238`, test at `tests/test_factory.py:835-858`) is a consequence of excluding `manager_failed` events from the selected escalation, not new behavior.

## Changed signatures / defaults — external consumers

- `Config.manager_cmd(prompt: str, cwd)` → `(prompt_path: Path, cwd)`: callers `factory/manage.py:266` and `factory/learn.py:68` both updated in the diff. No others (`grep manager_cmd` across `factory/`, `console/`, `skills/`, `tests/`). Holds.
- New `escalate` reason value `manager_failed`, consumers outside the diff:
  - `factory/dispatch.py:245-248` — `record("escalate", …)` inside the `manage` lifecycle scope sets `execution.outcome = "project_escalation"`, `reason = "manager_failed"` on the manage execution. `lifecycle.py:642` maps that to state `completed`; `manage.py:293-294` still overrides to `mechanism_failure` if `apply` fails. Holds; semantically odd (a manage stage reporting a project escalation outcome) but not incorrect. See optional.
  - `factory/learn.py:39` — "finished" detection via any `escalate` event. A `manager_failed` row only exists on tickets that already have a real escalation. Holds.
  - `factory/dispatch.py:587-593` (`sync_escalate`) — uses `escalation_packet` round arithmetic, which now excludes `manager_failed`. Holds.
- `stats.human_touch` return gains `manager_failures`: `factory/dashboard.py:789-792` passes the dict through verbatim (so the README claim at `README.md:144-146` holds); `dashboard.py:635,637` and `stats.human_touch_metrics` (`stats.py:189-190`) read only `escalation_count`/`escalation_times`, which now exclude failures as documented. Holds.
- Doctor row label `manager: <exe>` → `manager command` (with WARN when unset): `skills/factory/SKILL.md:44-47` instructs reporting every WARN with what it disables; the new WARN detail (`onboard.py:227`) supplies that. Holds. The old test at `tests/test_factory.py:393-401` was replaced.

## Required fixes

None.

## Optional suggestions

1. `factory/manage.py:279-280` — the `ConfigError` path (bare `{prompt}` for omp) posts a HUMAN diagnosis but records no `manager_failed` event, so `manager failures` in stats undercounts config-shaped failures. The spec only binds nonzero exits, so not required; recording the same event there would make the metric complete.
2. `factory/manage.py:273-274` — recording `escalate` inside the `manage` scope rewrites the manage execution's lifecycle outcome to `project_escalation` (`dispatch.py:245-248`). If lifecycle outcomes for the manage stage are consumed later, consider resetting `execution.outcome`/`reason` after the record or recording with a distinct event name. Not a defect today.
3. `factory/onboard.py:234` duplicates the omp check in `config.py:149`. Could delegate to `manager_cmd` + catch `ConfigError`.
4. `docs/manager-plan.md:242` describes the superseded doctor contract; a one-line note would prevent confusion.

VERDICT: APPROVE

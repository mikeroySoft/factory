## Standards

No `AGENTS.md`/`CONTRIBUTING.md` in repo. Sources checked: `docs/manager-plan.md` §4.3–4.4, `pyproject.toml` (`[tool.ruff] line-length = 100`), `README.md`.

- `manage.py:219` → `apply` at `manage.py:221`: `manage` event recorded before any `gh` mutation — satisfies plan §4.4 "decide → record → act".
- `manage.py:117-130`: FIX dispatches a `worker_round`, re-gates, pushes only on PASS, re-reviews, `approve_pr` only on fresh APPROVE — matches plan §4.3 rows "Dispatch a worker round" and "Re-add `factory-approved` … after gate PASS + fresh APPROVE". The manager itself never edits code.
- `manage.py:122,130`: FIX failure routes through `dispatch.escalate`, which bumps the packet `round`; `manage.py:189` then bounds re-entry by `manager_rounds`. Loop is bounded (plan §4.4).
- `README.md:88-101`: docs updated for the new decision and config shape.

Optional (style/config, not CI-enforced; `.github/workflows/ci.yml:11` runs only unittest, and `manage.py:56`, `:102`, `:211` already exceed the limit):
- `config.py:292`, `manage.py:109` exceed ruff `line-length = 100`.
- Baseline smell, judgement call: `manage.py:54` and `:65` re-subtract/re-check `RESERVED_LABELS` after `manage.py:199` already filtered them out. Harmless; defensible since `parse` is callable with an unfiltered dict.

## Spec

Issue #20 exit gate vs diff:

| Criterion | Status | Evidence |
|---|---|---|
| `[workers.<label>]` may carry `when` | ✓ | `config.py:289-298`; table-or-array handled per entry |
| Prompt lists labels with `when` | ✓ | `manage.py:199-201` |
| ROUTE/FIX accepted only for listed labels | ✓ | `manage.py:54-57`, `:64-66`; `parse` receives the filtered dict at `:213` |
| ROUTE chore without a chore worker rejected and recorded | ✓ | falls to `HUMAN` at `manage.py:72`, recorded at `:219`, diagnosis commented at `:157`; test `tests/test_factory.py:652-676` |
| With chore worker, label applied | ✓ | `manage.py:149-155`; test asserts `--add-label chore` at `tests/test_factory.py:673` |
| FIX on red CI runs `ci-fix` argv | ✓ | `manage.py:117-118` passes `{data["worker"]}` so `Config.worker` (`config.py:137`) selects that label; test `tests/test_factory.py:678-719` |
| Array-form `[workers]` still loads | ✓ | `config.py:290-291`; test `tests/test_factory.py:147-158` |
| `ci-fix` and `conflict` profiles in template, host-config, human-applied | ✓ | `factory/templates/factory.toml:37-47` (commented, with copy-to-host instructions) |
| `dashboard.py` touched | ✓ | `dashboard.py:847` exposes `worker_when` in `--json` config |

Scope creep: none. `RESERVED_LABELS` (`manage.py:32`) is a constant extracted from the existing set literal, not a new abstraction.

Optional:
- `config.py:290-291`: keys other than `command`/`when` inside a worker table are silently ignored (`KNOWN_KEYS["workers"] = None` at `config.py:58` means `unknown_keys` skips them too). A typo like `whne = "..."` yields "(no when rule)" with no drift report. Rejecting unknown keys in the loop would be two lines; not required by the issue.
- `manage.py:109`: FIX precondition failures raise `ValueError`, caught at `manage.py:222` as `mechanism_failure` with log only; the issue gets no comment, though the round is consumed. Consistent with existing SPLIT/REWRITE failure handling; not a spec rule.

## Changed signatures/defaults — consumers outside the diff

- `apply(n, issue, decision, body, data, packet)`: sole caller `manage.py:221` updated. Holds.
- `parse(output, workers)`: sole caller `manage.py:213`, now passes reserved-filtered dict. Holds; no test calls `manage.parse` directly.
- `Config.workers` type unchanged (`dict[str, list[str]]`): `config.py:137` `Config.worker`, `dashboard.py:698`, `dashboard.py:846`, `dashboard.py:873` → `stats.py:71-84`, `onboard.py:223-224` all still hold. Stricter validation (`config.py:292-293`) means `onboard.py:224` `argv_t[0]` can no longer `IndexError` on an empty argv.
- `Config.worker_when` (new field): consumed only by `manage.py:201` and `dashboard.py:847`; `dashboard.html` does not read `config.worker_when` — no breakage, just unrendered.
- `dispatch.worker_round` signature unchanged; new caller `manage.py:117-120` supplies all 7 positionals. `lifecycle.current().attempt` set at `dispatch.py:926` is a valid attribute on the `manage` scope's `Execution` (`lifecycle.py:245`).
- Merge stage `dispatch.py:785-808` keys on PR labels only, so a FIX-approved PR merges next pass without the issue leaving `ready-for-human`, as the README states (`README.md:98-99`).

## Required fixes

None.

VERDICT: APPROVE

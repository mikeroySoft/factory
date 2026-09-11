## Standards

- `factory/config.py:60` — `manager` already registered in `KNOWN_KEYS`; new keys `rounds`, `review` appended, so `unknown_keys` covers drift. Matches existing table pattern.
- `factory/config.py:287-291` — loader follows the existing `int(table.get(...))`/`Path(...)` style used for `gate`; `review` validation raises `ConfigError` like the neighbouring `manager.model` check (`factory/config.py:251`). Consistent.
- `factory/config.py:224-252` — `manager_model` → `manager_settings` rename migrates the only caller (`factory/config.py:287`) and the briefing test (`tests/test_briefing.py:116-124`); no alias left behind. Clean cutover.
- `factory/dashboard.py:840-844` — joins argv the same way as `reviewer` (`factory/dashboard.py:839`); `None` when disabled.
- `factory/onboard.py:226-227` — same `report(shutil.which(...))` shape as the reviewer row.

Not observable in the diff: `HOST_TABLES` is unchanged. `tests/test_factory.py:279-281` loads `[defaults.manager]` from the host file and asserts the values, and the gate passed, so `manager` is already a host table at the base commit (README also documents `[defaults.manager]`). No finding.

## Spec (issue #12 exit gate)

| Criterion | Evidence |
|---|---|
| `command` argv template, unset = disabled | `factory/config.py:104` default `None`; `factory/config.py:229-238` accepts list or shlex string |
| `rounds = 1` | `factory/config.py:105`, `factory/config.py:288` |
| `review = "escalated"`, `"all"` allowed, others rejected | `factory/config.py:106`, `factory/config.py:289-291`; negative case `tests/test_factory.py:287` |
| doctor prints `manager:` only when configured | `factory/onboard.py:226-227`; `tests/test_factory.py:294-302` asserts absent then `PASS` |
| unknown `[manager]` keys are drift | `tests/test_factory.py:252`, `tests/test_factory.py:263` |
| `dashboard --json` `config` includes table | `factory/dashboard.py:840-844` |
| template documents keys | `factory/templates/factory.toml:43-48` |
| loader test for all three keys | `tests/test_factory.py:112-115`, `tests/test_factory.py:137-138` |
| command not executed (out of scope) | `tests/test_briefing.py:119-121` marker check retained |

Minor, non-blocking: `rounds` is coerced with `int()` (`factory/config.py:288`), so a non-numeric value raises `ValueError` rather than `ConfigError` — identical to the existing `gate.timeout`/`dashboard.port` handling, so it is the repo's convention, not a defect here.

VERDICT: APPROVE

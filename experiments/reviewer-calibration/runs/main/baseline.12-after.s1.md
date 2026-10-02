## Spec (issue #12)

| Criterion | Evidence | Status |
|---|---|---|
| `[manager]` keys `command`, `rounds`, `review` registered | `factory/config.py:60` (`KNOWN_KEYS["manager"]`) | OK |
| `command` unset = disabled; `rounds` default 1; `review` default `"escalated"` | `factory/config.py:104-106` | OK |
| `"all"` allowed, other values rejected | `factory/config.py:290-291` | OK |
| Loader populates all three | `factory/config.py:287-289` | OK |
| `factory doctor` prints `manager:` row only when configured | `factory/onboard.py:226-227` | OK |
| `dashboard --json` `config.manager` | `factory/dashboard.py:840-844` | OK |
| `templates/factory.toml` documents keys, `{prompt}`/`{cwd}` | `factory/templates/factory.toml:43-48` | OK |
| Unknown keys under `[manager]` reported as drift | `tests/test_factory.py:252`, `:263` | OK |
| Loader test for all three keys | `tests/test_factory.py:112-115`, `:137-138` | OK |
| Doctor row present/absent test | `tests/test_factory.py:293-301` | OK |
| Host-layer (`[defaults.manager]`) loading | `tests/test_factory.py:279-282` exercises it; `HOST_TABLES` not in diff — `[INFERENCE]` already contained `manager` on base since `KNOWN_KEYS["manager"]` pre-existed and README documents `[defaults.manager]` | OK |
| Out of scope: running the command | `manager_settings` only parses/normalizes (`factory/config.py:224-252`); regression `tests/test_briefing.py:116-124` | OK |

Non-blocking observation (not an acceptance criterion): `factory/config.py:288` — `int(manager.get("rounds", ...))` raises `ValueError`, not `ConfigError`, on a non-numeric `rounds`. Matches the existing pattern for `gate.timeout` / `dashboard.port` (`:292`, `:305`), so consistent with repo convention.

## Standards

No AGENTS.md/CONTRIBUTING.md at this head. README conventions followed: `config.py` remains the single source of config values; dashboard `config` rendering matches the existing `reviewer`/`workers` string-join style (`factory/dashboard.py:838-841`); template comments preserve the "dashboard never executes manager.command" contract (`factory/templates/factory.toml:39-45`).

VERDICT: APPROVE

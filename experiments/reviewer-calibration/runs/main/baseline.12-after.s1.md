## Spec (issue #12)

| Criterion | Evidence | Status |
|---|---|---|
| `[manager]` keys `command`, `rounds`, `review` registered in `KNOWN_KEYS` | `factory/config.py:60` | ✓ |
| `Config` fields with defaults (`rounds = 1`, `review = "escalated"`, command unset = disabled) | `factory/config.py:104-106` | ✓ |
| Loader reads all three; rejects `review` outside `{"escalated","all"}` | `factory/config.py:287-291` | ✓ |
| Argv normalized (list or shell string), never executed; `--model` fallback preserved | `factory/config.py:224-252`; `tests/test_briefing.py:116-124` | ✓ |
| `factory doctor` prints `manager:` row only when configured | `factory/onboard.py:226-227`; `tests/test_factory.py:294-302` | ✓ |
| Unknown keys under `[manager]` reported as drift | `tests/test_factory.py:252,263` (relies on existing `unknown_keys` + `KNOWN_KEYS` tuple) | ✓ |
| `dashboard --json` `config` includes the table | `factory/dashboard.py:840-844` | ✓ |
| `templates/factory.toml` documents command/rounds/review with `{prompt}`/`{cwd}` semantics | `factory/templates/factory.toml:43-48` | ✓ |
| Loader test for all three keys in `tests/test_factory.py` | `tests/test_factory.py:112-115,137-138` | ✓ |
| Host-layer table (`HOST_TABLES`) | Not touched in diff. `[defaults.manager]` already documented as host-wide in README ("host-wide: `[defaults.manager]`") and `tests/test_factory.py:279-281` loads `command` from `[defaults.manager]` successfully under the passing gate → `manager` already in `HOST_TABLES` at base. `[INFERENCE]` from test behavior; no diff change needed. | ✓ |
| Out of scope: running the command | No execution path added; `manager_settings` docstring and `test_briefing.py:116-120` marker test assert non-execution | ✓ |

## Standards

No AGENTS.md/CONTRIBUTING.md at this head. README conventions: `config.py` remains single source of config values; template documents keys; `--model` read-only behavior for briefings preserved (`factory/config.py:239-240`, template `factory/templates/factory.toml:40-41`).

## Notes (non-blocking)

- `factory/config.py:288` — `int(manager.get("rounds", …))` raises `ValueError` rather than `ConfigError` on a non-integer `rounds`. Identical to existing `check_timeout`/`dashboard_port` handling (`factory/config.py:292,305`); consistent with repo pattern, not an acceptance criterion.

No findings that fail the exit gate or documented conventions.

VERDICT: APPROVE

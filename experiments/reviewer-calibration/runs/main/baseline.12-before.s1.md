## Spec (issue #12)

| Criterion | Evidence | Status |
|---|---|---|
| `command` argv template, unset = disabled | `factory/config.py:104` (`manager: list[str] \| None = None`), `factory/config.py:285-286` (set only if `"command" in manager`) | met |
| `rounds = 1` default | `factory/config.py:105`, `factory/config.py:287` | met |
| `review = "escalated"` default, `"all"` allowed | `factory/config.py:106`, `factory/config.py:288` | met (no value validation; exit gate doesn't require it — see note) |
| Unknown `[manager]` keys reported as drift | `factory/config.py:60` (`KNOWN_KEYS["manager"]` lists all four keys); `tests/test_factory.py:252`, `:263` assert `manager.unknown` appears in `.factory.toml keys` WARN detail | met |
| `factory doctor` prints `manager:` row only when configured | `factory/onboard.py:226-227` guarded by `if cfg.manager:`; `tests/test_factory.py:276-284` covers both absent and present | met |
| `dashboard --json` `config` includes table | `factory/dashboard.py:840-844` (`command`, `rounds`, `review`) | met |
| `templates/factory.toml` documents it | `factory/templates/factory.toml:43-48` documents `{prompt}`/`{cwd}`, `command`, `rounds`, `review` (`"all"`) | met |
| Loader test for all three keys | `tests/test_factory.py:112-115` fixture, `:137-138` assertions | met |
| Out of scope: running the command | No execution path added; template explicitly states running is separate (`factory/templates/factory.toml:44`) | respected |

`HOST_TABLES`: the diff does not touch it. `[INFERENCE]` `KNOWN_KEYS["manager"]` already existed on `origin/main` (`factory/config.py:60` context line `-"manager": ("model", "command")`) and README documents `[defaults.manager]` as host-wide, so `manager` is very likely already registered in `HOST_TABLES`. Not citable from the diff; not counted as a finding.

## Standards (README.md)

- `config.py` remains the single source of config values; `onboard.py`/`dashboard.py` only read `cfg.*` (`factory/onboard.py:227`, `factory/dashboard.py:841-843`). Consistent.
- Loader pattern mirrors existing `review.command` handling (`factory/config.py:283-286`) and `int()` coercion used for `gate.timeout` (`factory/config.py:287` vs `:289`). Consistent.
- Doctor row format matches `reviewer:` row (`factory/onboard.py:225` vs `:227`). Consistent.

## Non-blocking note

- `factory/config.py:288`: `manager_review` accepts any string; issue wording ("`"all"` allowed") implies a two-value domain. Not in the exit gate, so not a REVISE trigger.

VERDICT: APPROVE

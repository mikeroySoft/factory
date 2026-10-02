## Review: issue #12 `[manager]` config table

### Spec coverage

| Criterion | Evidence | Status |
|---|---|---|
| Three keys `command`, `rounds`, `review` in `KNOWN_KEYS` | `factory/config.py:60` | met |
| `Config` fields with defaults `rounds = 1`, `review = "escalated"`; unset `command` = disabled (`None`) | `factory/config.py:104-106` | met |
| Loader reads all three; `review` restricted to `escalated`/`all` | `factory/config.py:287-291` | met |
| `command` argv template; string form shlex-split; never executed | `factory/config.py:224-252`, test `tests/test_briefing.py:116-124` | met |
| `factory doctor` prints `manager:` row only when configured | `factory/onboard.py:226-227`; test `tests/test_factory.py:294-302` | met |
| Unknown keys under `[manager]` reported as drift | `tests/test_factory.py:252`, `:263` (relies on existing `unknown_keys` over `KNOWN_KEYS`) | met |
| `dashboard --json` `config` includes table | `factory/dashboard.py:840-844` | met |
| `templates/factory.toml` documents keys and placeholders | `factory/templates/factory.toml:43-48` | met |
| Loader test for all three keys | `tests/test_factory.py:112-115`, `:137-138` | met |
| Host-layer (`HOST_TABLES`) | Not in diff. `tests/test_factory.py:279-281` loads `[defaults.manager]` via host file and asserts `cfg.manager`/`cfg.manager_model` resolve; README already documents `[defaults.manager]` for `manager.model`. `[INFERENCE]` `manager` was already registered in `HOST_TABLES` at base for the pre-existing `model` key; the test exercises that path and the gate passed. | met |
| Out of scope: running the command | No execution path added; `manager_settings` docstring and test `tests/test_briefing.py:116-121` assert non-execution | respected |

### Required fixes

None.

### Optional suggestions

1. `factory/config.py:288` — `int(manager.get("rounds", ...))` raises bare `ValueError` on a non-integer value (e.g. `rounds = "two"`), surfacing as a traceback instead of `ConfigError`. Same pattern as existing `gate.timeout`/`dashboard.port` (`factory/config.py:292`, `:305`), so this follows repo convention; flagging only because `review` on the adjacent line *does* get a `ConfigError`. Not blocking.
2. `factory/config.py:231-239` — `command` is now validated regardless of whether `model` is set (previously parsed only when `model is None`). This is a correct tightening given `command` is now a first-class key, but it means a host file with `model` set and a malformed legacy `command` now fails at load where it previously loaded. Intentional per `tests/test_factory.py:283-291`; noting as a behavior change, not a defect.

No unrequested abstractions introduced; `manager_model` → `manager_settings` rename returns the already-parsed argv instead of discarding it, avoiding a second parse of `command`.

VERDICT: APPROVE

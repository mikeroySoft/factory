## Standards

No AGENTS.md/CONTRIBUTING.md at this head; README conventions: every `.factory.toml` key optional, `config.py` single source of repo-specific values, template documents all keys, manager settings host-wide via `[defaults.manager]`. Diff conforms:

- Defaults keep every key optional — `factory/config.py:104-106`.
- Loader stays in `config.py`; consumers read `cfg.manager*` only — `factory/onboard.py:226-227`, `factory/dashboard.py:840-844`.
- Template documents all three keys with placeholder semantics — `factory/templates/factory.toml:43-48`.

## Spec (issue #12)

| Criterion | Evidence | Status |
|---|---|---|
| `command` argv template, unset = disabled | `factory/config.py:104` (`None` default), `:224-237` (string→argv via existing parser, list validated), doctor/dashboard gate on falsy `cfg.manager` | met |
| `rounds = 1` | `:105`, `:288` | met |
| `review = "escalated"`, `"all"` allowed, others rejected | `:106`, `:289-291` | met |
| `KNOWN_KEYS` | `:60` | met |
| `HOST_TABLES` | not touched by diff | see note |
| doctor `manager:` row only when configured | `factory/onboard.py:226-227`; `tests/test_factory.py:296-304` | met |
| unknown `[manager]` keys as drift | `tests/test_factory.py:252`, `:263` | met |
| `dashboard --json` `config.manager` | `factory/dashboard.py:840-844` | met |
| loader test for all three keys | `tests/test_factory.py:112-115`, `:137-138` | met |
| command not executed | `tests/test_briefing.py:116-124` (marker file never created) | met |

Note on `HOST_TABLES`: `manager` was already in `KNOWN_KEYS` at base (`factory/config.py:60` context) and README states `[manager].model` is host-wide via `[defaults.manager]`, so `[INFERENCE]` it was already registered in `HOST_TABLES`. The host-layer test `tests/test_factory.py:276-281` loads `command`/`model` from `[defaults.manager]` and the gate passed, which is consistent with that. Cannot confirm from the diff alone.

## Required fixes

None.

## Optional suggestions

- `factory/config.py:288`: `int(manager.get("rounds", ...))` turns a non-numeric `rounds = "x"` into a raw `ValueError` rather than `ConfigError`, and accepts `rounds = -1`/`true`. Same pattern as existing `gate.timeout` (`:292`) and `dashboard.port`, so this is repo convention, not a defect introduced here; only worth tightening if `rounds` later drives a loop bound in the run feature.
- `factory/config.py:224-237`: `command` is now validated even when `model` is set (previously only parsed when `model` was unset). Behavior change is stricter, not looser; fine, but note it in the PR description since a host with a malformed legacy `command` and an explicit `model` will now fail to load.
- `tests/test_factory.py:296-304`: `rows()` shadows nothing but the `# noqa: E731` lambda could be a nested `def`; style only.

No net-new abstractions introduced; `manager_settings` replaces `manager_model` one-for-one with the same callers (`factory/config.py:287`, `tests/test_briefing.py:119-124`).

VERDICT: APPROVE

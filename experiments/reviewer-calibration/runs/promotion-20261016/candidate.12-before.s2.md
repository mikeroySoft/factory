## Standards

Repo has no `AGENTS.md`/`CONTRIBUTING.md`; standards sources are `docs/manager-plan.md` and the in-file conventions of `factory/config.py`. Diff follows the existing `review`/`workers` loader shape (`list(raw["review"]["command"])` → `list(manager["command"])`), reuses `report()` in doctor, and extends `KNOWN_KEYS` so drift detection works without new machinery. `HOST_TABLES` already contained `"manager"` on main (`factory/config.py:50`), so the brief's "register in `HOST_TABLES`" is satisfied without a change. No baseline smells beyond the duplicated `raw.get("manager", {})` noted below.

## Spec

Exit gate items vs. diff:

| Criterion | Evidence | Status |
|---|---|---|
| `command`/`rounds`/`review` keys, defaults `1`/`"escalated"` | `factory/config.py:104-106`, `:285-288` | met |
| unset command = disabled | `factory/config.py:104` (`None`), `factory/onboard.py:226` | met |
| doctor prints `manager:` row only when configured | `factory/onboard.py:226-227`, test `tests/test_factory.py:276-285` | met |
| unknown `[manager]` keys reported as drift | `factory/config.py:60`, test `tests/test_factory.py:252,263` | met |
| `dashboard --json` `config` includes table | `factory/dashboard.py:840-844` | met (untested, not required) |
| template documents it | `factory/templates/factory.toml:40-48` | met |
| loader test for all three keys | `tests/test_factory.py:112-115,137-138` | met |
| out of scope: running the command | no executor added | met |

## Required fixes

1. **String-form `command` is mangled into a character list.** `factory/config.py:285-286` does `cfg.manager = list(manager["command"])`. The same table's pre-existing validator (`manager_model`, `factory/config.py:231-237`, run at `:302`) explicitly accepts `command` as a string ("must be an argv array or command string", shlex-split) and `README.md:171-172` documents that an "existing `manager.command`" is read. Trigger: `[manager] command = "omp -p --model x"` passes validation, but `cfg.manager` becomes `['o','m','p',' ', …]`. Impact: `factory doctor` reports `manager: o` with a `which("o")` verdict (`factory/onboard.py:227`), and `dashboard --json` shows `"o m p   - p …"` (`factory/dashboard.py:841`). Concrete correctness defect, not a style point. Fix either way, both one-liners: shlex-split a `str` command in `load` before `list(...)`, or make `manager_model` reject strings so the validator and loader agree. The latter changes documented behavior, so splitting in the loader is the smaller change.

## Optional suggestions

- `factory/config.py:288`: `review` accepts any string; issue text only allows `"escalated"`/`"all"`. A `ConfigError` on anything else would match how `manager_model` treats bad `model` values, but the brief does not require validation. Non-blocking.
- `factory/config.py:286`: non-list/non-str `command` (e.g. `command = 5`) raises `TypeError` at `list(5)` before the `ConfigError` at `:302` fires; ordering `cfg.manager_model = manager_model(...)` ahead of `:285` would keep the existing error path. Non-blocking.
- `factory/config.py:269` and `:302` both read `raw.get("manager", {})`; `:302` could use the local `manager`. Non-blocking.
- `README.md:170-174` describes only `[manager].model`; the new keys are documented in the template only, which is what the issue asked for. Non-blocking.

## Changed signatures / consumers outside the diff

- `Config.manager`, `manager_rounds`, `manager_review` (new fields): consumers are `factory/dashboard.py:840-844` and `factory/onboard.py:226-227` (both in diff); `factory/briefing.py:345-346,430` use only the untouched `manager_model`. Holds.
- `KNOWN_KEYS["manager"]` widened (`factory/config.py:60`): consumer is doctor drift reporting; `[defaults.manager]` layering via `HOST_TABLES` (`factory/config.py:50,191`) unchanged. Holds.
- No defaults, flags, or call signatures changed.

VERDICT: REVISE

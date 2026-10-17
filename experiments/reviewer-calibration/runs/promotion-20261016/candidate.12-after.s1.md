README defers to the template ("documents them all"), so the template change covers docs.

## Standards

No `CONTRIBUTING.md`/`AGENTS.md` in repo; `README.md` defers config docs to `factory/templates/factory.toml` (README.md:118-119), which the diff updates. `pyproject.toml:33-35` sets ruff `line-length = 100` but no `select`, so E501 is not enforced; pre-existing test lines in `tests/test_factory.py` already exceed it. Nothing violates a documented rule.

Smell baseline (judgement calls only):
- `factory/config.py:224-252` — `manager_settings` returns a `(command, model)` tuple; callers index `[1]` (`tests/test_briefing.py:122`). Minor Data Clump/Mysterious tuple. Not worth an abstraction; acceptable for two call sites.
- `factory/config.py:104-106` — new `Config` fields lack the trailing comments the sibling fields carry (`config.py:97-99,109`). Cosmetic.

## Spec (issue #12 + agent brief)

| Criterion | Status | Evidence |
|---|---|---|
| `[manager] command` argv template, unset = disabled | ✅ | `config.py:104,229-238,287`; `[]`/unset → `None`-ish, doctor/dashboard treat falsy as disabled |
| `rounds = 1` default | ✅ | `config.py:105,288` |
| `review = "escalated"`, `"all"` allowed, else error | ✅ | `config.py:106,289-291`; tested `tests/test_factory.py:286` |
| Host-layer table (`HOST_TABLES`) | ✅ already on main | `config.py:50` contains `"manager"` pre-diff; host-layer load tested `tests/test_factory.py:279-282` |
| `KNOWN_KEYS` + unknown keys → drift | ✅ | `config.py:60`; `tests/test_factory.py:252,263` |
| `factory doctor` `manager:` row only when configured | ✅ | `onboard.py:226-227`; `tests/test_factory.py:295-303` |
| `dashboard --json` `config` includes table | ✅ | `dashboard.py:840-844` under `"config"` (dashboard.py:822) |
| `templates/factory.toml` documents it | ✅ | `templates/factory.toml:40-48` |
| Loader test for all three keys | ✅ | `tests/test_factory.py:112-115,137-138` |
| Out of scope: running the command | ✅ | no `manager_cmd`/`expand` for manager; placeholders only documented |

No scope creep. The `command` string form (shlex) is carried over from pre-existing `manager_model` behaviour, not new.

### Changed signature: `manager_model(table) -> str` → `manager_settings(table) -> (argv, model)`
Consumers outside the diff:
- `factory/briefing.py:345-346,430` — reads `cfg.manager_model` only. Model derivation is unchanged: explicit `model` wins, else `--model`/`--model=` scanned from `command` (`config.py:239-246`), same validation (`config.py:247-251`). Still holds.
- `factory/onboard.py:131,140,149` — `HOST_TABLES` consumers, untouched set. Still holds.
- No remaining references to `manager_model(` (grep over HEAD).

## Required fixes
None.

## Optional suggestions
- `factory/config.py:288` — `int(manager.get("rounds", …))` on a non-numeric value raises bare `ValueError` instead of `ConfigError`. Matches existing `gate.timeout`/`dashboard.port` handling (`config.py:292,305`), so consistent; only flag if you want uniform error messages later.
- `factory/config.py:104-106` — add the one-line trailing comments the neighbouring fields use (e.g. `# frontier manager argv; None = disabled`).

VERDICT: APPROVE

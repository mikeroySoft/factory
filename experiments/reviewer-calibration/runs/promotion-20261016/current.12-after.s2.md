## Standards

Sources: no `AGENTS.md`/`CONTRIBUTING.md` in repo; `pyproject.toml` (`[tool.ruff]`, default rule set, no E501), `README.md`, `docs/manager-plan.md`. CI runs no linter.

**Required:** none.

**Optional**
- `factory/config.py:288` — `int(manager.get("rounds", …))` raises bare `ValueError` on `rounds = "x"` rather than `ConfigError`, and accepts `0`/negatives. Matches the existing pattern at `config.py:292` (`gate.timeout`) and `:305` (`dashboard.port`), so consistent; only worth tightening if T4 needs `rounds >= 1`.
- `README.md:133` shows a `[review]` block in the `.factory.toml` walkthrough but no `[manager]` block. Issue only requires `templates/factory.toml` documentation (done at `factory/templates/factory.toml:40-48`), so non-blocking.
- `tests/test_factory.py:299` — lambda-with-`noqa` is a judgement call (Mysterious Name/one-off helper); pre-existing tests use the same inline-dict idiom, no rule broken.

Smell baseline: no Duplicated Code, Speculative Generality, or Shotgun Surgery beyond the touch list the issue itself enumerates. `manager_settings` replacing `manager_model` (`config.py:224-252`) is a widening of an existing function, not a new abstraction; the legacy `--model` extraction and non-execution guarantee are preserved (`tests/test_briefing.py:116-124`).

## Spec

Issue #12 brief vs diff:

| Criterion | Evidence | Status |
|---|---|---|
| `command` argv template, unset = disabled | `config.py:104`, `:224-238` (list or shlex string, type-checked); `onboard.py:226-227`, `dashboard.py:841` treat `None`/empty as disabled | met |
| `rounds = 1` default | `config.py:105`, `:288` | met |
| `review = "escalated"`, `"all"` allowed, others rejected | `config.py:106`, `:289-291`; `tests/test_factory.py:287` | met |
| Host-layer table (`HOST_TABLES`) | `config.py:50` already contained `"manager"` on `origin/main`; host layering exercised via `[defaults.manager]` in `tests/test_factory.py:279-282` | met |
| `KNOWN_KEYS` updated; unknown keys → drift | `config.py:60`; `tests/test_factory.py:252,263` (`manager.unknown`) | met |
| `factory doctor` `manager:` row only when configured | `onboard.py:226-227`; `tests/test_factory.py:296-304` | met |
| `dashboard --json` `config` includes table | `dashboard.py:840-844` | met |
| `templates/factory.toml` documents it | `templates/factory.toml:40-48` | met |
| Loader test for all three keys | `tests/test_factory.py:112-115,137-138` | met |
| Out of scope: running the command | no subprocess call added; `tests/test_briefing.py:116-120` asserts non-execution | met |

Scope creep: none. `model` key retained in `KNOWN_KEYS` (`config.py:60`) is pre-existing behaviour for briefings, not new.

Required: none. Optional: see Standards.

VERDICT: APPROVE

## Standards

No `AGENTS.md`/`CONTRIBUTING.md` in the repo; conventions are the existing code patterns, `pyproject.toml` (`[tool.ruff] line-length = 100`, default rule set — E501 not selected, CI runs only `unittest`), and `docs/manager-plan.md`.

- `factory/config.py:287-291` — manager keys loaded in the same style as sibling tables (`int(table.get(...))`, inline `ConfigError` on invalid enum). Consistent.
- `factory/dashboard.py:840-844` / `factory/onboard.py:226-227` — mirror the existing `reviewer` rows. Consistent.
- `factory/templates/factory.toml:43-48` — commented defaults, same shape as `[review]`/`[workers]` (also `HOST_TABLES`). Consistent.
- Smell check: `manager_settings` (`factory/config.py:224-252`) merges the pre-existing `manager_model` parsing with argv normalization instead of parsing `command` twice — a consolidation, not a new abstraction. No baseline smells beyond that.

## Spec (issue #12 + agent brief)

| Criterion | Evidence | Status |
|---|---|---|
| `command` argv template, unset = disabled | `factory/config.py:104,229-238,287`; `onboard.py:226` gates on truthiness | met |
| `rounds = 1` default | `config.py:105,288` | met |
| `review = "escalated"`, `"all"` allowed, others rejected | `config.py:106,289-291`; `tests/test_factory.py:287` | met |
| Host-layer table (`HOST_TABLES`) | already present at base (`config.py:50`); `KNOWN_KEYS` extended `config.py:60` | met |
| `factory doctor` prints `manager:` row only when configured | `onboard.py:226-227`; `tests/test_factory.py:296-304` | met |
| Unknown `[manager]` keys reported as drift | `tests/test_factory.py:252,263` via existing `unknown_keys` | met |
| `dashboard --json` `config` includes it | `dashboard.py:840-844` | met (untested, see optional) |
| `templates/factory.toml` documents it | `templates/factory.toml:43-48` | met |
| Loader test for all three keys in `tests/test_factory.py` | `tests/test_factory.py:112-115,137-138` | met |
| Out of scope: running the command | no exec path added; `test_briefing.py:118-121` still asserts non-execution | respected |

No scope creep. Legacy `--model` extraction behaviour from base is preserved (`config.py:239-246`, `tests/test_factory.py:279-282`).

## Plan drift

Plan status: not recorded — nothing to diff against. Relative to the issue's **Touches** list, extra files: `tests/test_factory.py` (required by the exit gate) and `tests/test_briefing.py:116-124` (mechanical follow-through of the `manager_model` → `manager_settings` rename; `grep` shows no other callers). No defect exposed.

## Required fixes

None.

## Optional suggestions

- `factory/config.py:288` — `rounds` accepts `0`/negative; sibling `check_timeout` is equally unvalidated, so this matches repo convention. Only worth a guard when T4 consumes it.
- `factory/dashboard.py:840-844` — no test asserts the `manager` block in `snapshot()["config"]`. Exit gate doesn't require one.

VERDICT: APPROVE

## Standards

Sources checked: `README.md` (Configuration §, lines 116–160: "The template written by `factory init` documents them all"), `docs/manager-plan.md` (T3, lines 237–243), `factory/config.py` header comments (lines 45–54). No `AGENTS.md`/`CONTRIBUTING.md` in repo.

No hard violations.

Judgement calls (non-blocking):
- **Mysterious Name** — `Config.manager: list[str] | None` (`factory/config.py:104`) holds argv while siblings are `manager_model`/`manager_rounds`/`manager_review`; the existing argv field for review is `reviewer`. Every other `Config` field with non-obvious semantics carries an inline comment (`config.py:109,114,116`); the three new fields have none. Optional: comment or rename (`manager_argv`).
- `cfg.manager_rounds = int(manager.get("rounds", ...))` (`config.py:288`) accepts negative/zero and raises `ValueError` not `ConfigError` on non-numeric — identical to `gate.timeout`/`dashboard.port` handling (`config.py:292,305`), so this matches existing convention. Not a finding.

## Spec

Issue #12 + agent brief, checked against the diff:

| Criterion | Status | Evidence |
|---|---|---|
| `[manager] command` argv template, unset = disabled | ✓ | `config.py:104,224–252,287` |
| `rounds = 1` default | ✓ | `config.py:105,288` |
| `review = "escalated"`, `"all"` allowed, others rejected | ✓ | `config.py:106,289–291` |
| Host-layer table in `HOST_TABLES` | ✓ (already on main) | `config.py:50` |
| `KNOWN_KEYS` extended | ✓ | `config.py:60` |
| `factory doctor` prints `manager:` row only when configured | ✓ | `onboard.py:226–227`; test `tests/test_factory.py:295–303` |
| Unknown `[manager]` keys reported as drift | ✓ | `tests/test_factory.py:252,263` |
| `dashboard --json` `config` includes the table | ✓ | `dashboard.py:840–844` |
| `templates/factory.toml` documents it | ✓ | `factory/templates/factory.toml:39–48` |
| Loader test covering all three keys | ✓ | `tests/test_factory.py:112–115,137–138` |
| Out of scope: running the command | ✓ not implemented | `manager_settings` docstring `config.py:225`; test `tests/test_briefing.py:116–124` |

Scope beyond brief: `manager_model` → `manager_settings` refactor (`config.py:224`) returning `(command, model)`. Justified: the pre-existing legacy `--model` extraction already parsed `command`; a second parser would duplicate the shlex/argv validation. Also preserves `command` as string form (`tests/test_factory.py:279–281`). Not an unrequested abstraction.

Minor observations (non-blocking):
- `dashboard.py:841` joins argv with spaces, lossy for args containing whitespace (`"{prompt} with spaces"` case tested at `tests/test_factory.py:281`). Same as `workers`/`reviewer` on lines 838–839; display only.
- `test_doctor_reports_manager_only_when_configured` commits `[manager]` into the repo `.factory.toml` (`tests/test_factory.py:302`), which doctor also flags as "host settings committed" — unasserted, harmless.

## Required fixes

None.

## Optional suggestions

1. `factory/config.py:104–106` — inline comments matching `config.py:114` style, or rename `manager` → `manager_argv`.

Summary: Standards 0 hard / 1 judgement call; Spec 0 missing, 0 wrong, 0 creep.

VERDICT: APPROVE

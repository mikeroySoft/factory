No placeholder validation exists for `[workers]`/`[review]` either; `[manager]` matches that convention.

## Standards

Sources checked: `docs/manager-plan.md` T3 (lines 236-244), `README.md` (config, doctor, briefing sections), `factory/templates/factory.toml`, `pyproject.toml` `[tool.ruff] line-length = 100`, `.github/workflows/ci.yml`. No `AGENTS.md`/`CONTRIBUTING.md` in the repo.

- Host-layer convention: `HOST_TABLES` already contained `"manager"` on main (`factory/config.py:50`); the diff correctly only extends `KNOWN_KEYS` (`factory/config.py:60`). `[manager]` written to a repo file is reported as drift via the existing `unknown_keys` path (`tests/test_factory.py:252,263`).
- Loader style matches siblings: `int(manager.get("rounds", …))` mirrors `int(gate.get("timeout", …))` (`factory/config.py:288,292`); doctor row `manager: <bin>` mirrors `reviewer: <bin>` (`factory/onboard.py:226-227`); dashboard joins argv like `workers`/`reviewer` (`factory/dashboard.py:840-844`).
- Rename `manager_model` → `manager_settings`: all callers updated (`factory/config.py:287`, `tests/test_briefing.py:119-124`); no other references in `factory/`, `tests/`, `skills/`, docs.
- Test isolation: `HostConfigTest.tearDown` clears the host file (`tests/test_factory.py:158-159`), so the leaked `[defaults.manager]` in `test_manager_legacy_command_and_invalid_settings` (`tests/test_factory.py:279`) does not bleed into `test_doctor_reports_manager_only_when_configured` under either unittest or pytest ordering.
- Smell baseline: nothing beyond judgement-call noise. `manager_settings` returning a tuple is a minor Data Clump but one call site; not worth a type.

Optional:
- `tests/test_factory.py:229` (142 cols), `:279` (144), `:252` (168) exceed `[tool.ruff] line-length = 100`. Not enforced by CI (`ci.yml` runs unittest only) and 349 pre-existing lines already violate it; `tests/test_briefing.py:119,122` were already over before this diff. Non-blocking.

## Spec (issue #12 + agent brief)

| Criterion | Status | Evidence |
|---|---|---|
| `[manager]` host-layer table like `review`; `HOST_TABLES` + `KNOWN_KEYS` | Met | `config.py:50` (pre-existing), `config.py:60` |
| `command` argv template, `{prompt}`/`{cwd}`, unset = disabled | Met | `config.py:104,229-238`; `onboard.py:226` and `dashboard.py:841` treat `None`/empty as disabled |
| `rounds` default 1 | Met | `config.py:105,288` |
| `review` default `"escalated"`, `"all"` allowed, else error | Met | `config.py:106,289-291`; `test_factory.py:288` |
| `factory doctor` prints `manager:` row when configured, nothing otherwise | Met | `onboard.py:226-227`; `test_factory.py:295-303` |
| Unknown keys under `[manager]` reported as drift | Met | `test_factory.py:252,263` |
| `dashboard --json` `config` includes the table | Met | `dashboard.py:840-844` (no test; exit gate doesn't require one) |
| `templates/factory.toml` documents it | Met | `templates/factory.toml:43-48` |
| Loader test for all three keys | Met | `test_factory.py:112-115,137-138` |
| Do NOT run the command | Met | no `subprocess`/`run` of `cfg.manager` anywhere in diff |
| `uv run pytest tests/test_factory.py` passes | Gate: test PASS | authoritative |

Scope beyond brief (not defects): legacy string-form `command` accepted via `shlex.split` and the briefing `--model` fallback retained (`config.py:231-247`) — this is preservation of pre-existing main behavior, not new abstraction. `command` is now validated even when `model` is set (previously skipped); stricter, no criterion violated.

## Plan drift

Plan status: not recorded — no worker plan to diff against. Against the issue's **Touches** list: `factory/config.py`, `factory/onboard.py`, `factory/templates/factory.toml`, `factory/dashboard.py` all named. Unnamed files changed: `tests/test_factory.py` (required by exit gate), `tests/test_briefing.py` (forced by the `manager_model` rename; without it the suite would break). Neither exposes a defect. Gate report lists `test: PASS`; the exit gate's named proof (`tests/test_factory.py` with loader test) is present in the diff.

## Required fixes

None.

## Optional suggestions

1. `tests/test_factory.py:229,252,279` — over the documented 100-col ruff limit; wrap if the repo ever starts enforcing ruff in CI.
2. `factory/dashboard.py:840-844` — `config.manager` has no `dashboard --json` assertion; `test_defaults_from_origin`/`DashboardTest` could pin `{"command": None, "rounds": 1, "review": "escalated"}` in one line if you want the JSON shape locked.

VERDICT: APPROVE

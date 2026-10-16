## Standards

No AGENTS.md/CONTRIBUTING.md at this head; README is the only documented convention source. Diff follows existing `config.py` patterns: `KNOWN_KEYS` registration (`factory/config.py:60`), `Config` fields with dataclass defaults (`factory/config.py:104-106`), loader reads via `raw.get(...)` + `int(...)` coercion matching `gate.timeout`/`dashboard.port` (`factory/config.py:287-291`), doctor `report(shutil.which(argv[0]) ...)` mirroring the reviewer row (`factory/onboard.py:226-227`), dashboard snapshot joining argv like `reviewer` (`factory/dashboard.py:840-844`). Template comment style matches surrounding sections (`factory/templates/factory.toml:43-48`). No new abstractions; `manager_model` → `manager_settings` is a rename/extension of an existing helper, not a net-new layer.

## Spec (issue #12 exit gate)

- `[manager] command` argv template, unset = disabled: `factory/config.py:104`, `:287`; `cfg.manager is None` when absent, `[]`/falsy also treated as disabled by doctor/dashboard. ✔
- `rounds = 1`, `review = "escalated"` with `"all"` allowed, other values rejected: `factory/config.py:105-106`, `:288-291`. ✔
- Doctor prints `manager:` row only when configured: `factory/onboard.py:226-227`; tested both branches `tests/test_factory.py:295-303`. ✔
- Unknown keys under `[manager]` reported as drift: relies on existing `unknown_keys` + `KNOWN_KEYS` entry `factory/config.py:60`; asserted `tests/test_factory.py:252`, `:263`. ✔
- `dashboard --json` `config.manager` with all three keys: `factory/dashboard.py:840-844`. ✔
- Template documents all three keys: `factory/templates/factory.toml:43-48`. ✔
- Loader test for all three keys: `tests/test_factory.py:112-115`, `:137-138`. ✔
- Running the command: not implemented; `manager_settings` docstring and test `tests/test_briefing.py:116-124` confirm never executed. ✔
- `HOST_TABLES`: untouched in diff. `[INFERENCE]` `manager` was already a host table at base (README documents `[defaults.manager]`; `tests/test_factory.py:279-282` loads `command` from `[defaults.manager]` and the gate passed), so no change was required.

## Required fixes

None.

## Optional suggestions

- `factory/config.py:288` — `int(manager.get("rounds", …))` raises `ValueError` rather than `ConfigError` on a non-integer `rounds`. Same behaviour as existing `gate.timeout`/`dashboard.port`, so consistent with repo convention; mention only because `review` gets a `ConfigError` while `rounds` doesn't (`tests/test_factory.py:283-288` covers `review`, not `rounds`).
- `factory/config.py:287` — no check that `command` contains `{prompt}`. Issue text calls it an "argv template with `{prompt}`, `{cwd}`" but states no validation criterion and execution is out of scope; leave as is unless the T3 plan requires it.

VERDICT: APPROVE

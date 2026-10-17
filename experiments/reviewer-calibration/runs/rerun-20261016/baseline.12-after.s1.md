## Spec (issue #12)

- `command` argv template, unset = disabled: `factory/config.py:104` (`manager: list[str] | None = None`), `factory/config.py:287` populates via `manager_settings`; `factory/onboard.py:226-227` gates the doctor row on `cfg.manager`. Satisfied.
- `rounds = 1` default, `review = "escalated"` with `"all"` allowed and other values rejected: `factory/config.py:105-106`, `factory/config.py:288-291`. Satisfied.
- Unknown keys under `[manager]` as drift: `factory/config.py:60` extends `KNOWN_KEYS["manager"]`; regression at `tests/test_factory.py:252`, `tests/test_factory.py:263`. Satisfied.
- `factory doctor` prints `manager:` row only when configured: `factory/onboard.py:226-227`; test `tests/test_factory.py:295-303`. Satisfied.
- `dashboard --json` `config.manager`: `factory/dashboard.py:840-844`. Satisfied.
- Template documents all three keys and placeholders: `factory/templates/factory.toml:43-48`. Satisfied.
- Loader test for all three keys: `tests/test_factory.py:112-115`, `tests/test_factory.py:137-138`. Satisfied.
- Out of scope respected: `manager_settings` docstring/implementation never executes (`factory/config.py:224-225`), test `tests/test_briefing.py:116-124` keeps the no-execution guard.

Not a finding (no diff evidence either way): the issue lists `HOST_TABLES` as a touch point, but the diff does not modify it. `tests/test_factory.py:276-281` loads `[defaults.manager]` from the host layer and the gate passed, so `manager` is already host-layer at base; nothing to revise.

## Standards (README)

README `[review]`/`[workers]` document argv templates with `{prompt}`/`{cwd}`; the template entry at `factory/templates/factory.toml:45-46` follows that shape. `config.py` remains the single source of config values; dashboard/doctor read `cfg.manager*` only. No convention violations found.

VERDICT: APPROVE

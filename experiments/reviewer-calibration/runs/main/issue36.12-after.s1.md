# Review: issue #12 `[manager]` config table (head 7cd7a83)

## Spec coverage

| Criterion | Evidence | Status |
|---|---|---|
| `command` argv template, unset = disabled | `factory/config.py:104` (`manager: list[str] \| None = None`), `:287` (`cfg.manager, cfg.manager_model = manager_settings(manager)`) | Met |
| `rounds = 1` default | `factory/config.py:105`, `:288` | Met |
| `review = "escalated"`, `"all"` allowed, others rejected | `factory/config.py:106`, `:289-291` | Met |
| `KNOWN_KEYS` extended; unknown keys drift | `factory/config.py:60`; `tests/test_factory.py:252`, `:263` | Met |
| `factory doctor` prints `manager:` row only when configured | `factory/onboard.py:226-227`; `tests/test_factory.py:294-302` | Met |
| `dashboard --json` `config` includes table | `factory/dashboard.py:840-844` | Met |
| `templates/factory.toml` documents it | `factory/templates/factory.toml:43-48` | Met |
| Loader test for all three keys | `tests/test_factory.py:112-115`, `:137-138` | Met |
| Do not run the command | `manager_settings` never executes; `tests/test_briefing.py:116-124` retains the no-exec guard | Met |

`HOST_TABLES`: the diff does not touch it. `manager` was already a known table before this change (`factory/config.py:60` pre-image has `("model", "command")`) and README documents `[defaults.manager]` as host-wide, so it is presumably already registered. `[INFERENCE]` — not verifiable from the diff; the host-layer test at `tests/test_factory.py:279-282` passing on the gate host supports it.

## Required fixes

None.

## Optional suggestions

1. `factory/config.py:288` — `int(manager.get("rounds", ...))` accepts negatives and raises `ValueError` (not `ConfigError`) for non-numeric strings. This matches the existing `gate.timeout` pattern at `:292`, so it is consistent with repo convention; mentioning only because `review` at `:289-291` gets explicit validation while `rounds` does not.
2. `factory/config.py:239` — `command = []` (or `command = ""`) yields `cfg.manager == []`, which is falsy: doctor skips the row (`onboard.py:226`) and dashboard reports `command: null` (`dashboard.py:841`). That matches "unset = disabled" semantics; no action needed unless an empty argv should be rejected.
3. `factory/config.py:224` — behaviour change: `command` is now parsed/validated even when `model` is set (previously only when `model` was `None`). Correct for this feature (the argv must be retained), and covered by `tests/test_factory.py:279-282`. Note that a previously-tolerated malformed `command` alongside a valid `model` now raises `ConfigError`; the drift report already flags unknown keys, so surfacing bad argv early is the safer choice.

No net-new abstractions introduced; `manager_model` → `manager_settings` is a rename/widening of the existing helper with all callers migrated (`config.py:287`, `tests/test_briefing.py:119-124`).

VERDICT: APPROVE

# Review: issue #12 `[manager]` config table (head 7cd7a83)

## Spec coverage

| Criterion | Evidence | Status |
|---|---|---|
| `command` argv template, unset = disabled | `factory/config.py:104` (`manager: list[str] \| None = None`), `:226-239` parse/validate, `:287` assign | Met |
| `rounds = 1` default | `factory/config.py:105`, `:288` | Met |
| `review = "escalated"`, `"all"` allowed, others rejected | `factory/config.py:106`, `:289-291` | Met |
| `KNOWN_KEYS` extended; unknown keys → drift | `factory/config.py:60`; test `tests/test_factory.py:252`, `:263` | Met |
| `HOST_TABLES` registration | Not in diff. Pre-existing README ("host-wide: `[defaults.manager]`") and the new host-file test at `tests/test_factory.py:279-282` (gate PASS) indicate `manager` was already host-layered before this change. `[INFERENCE]` — not verifiable from the diff alone. | Met (pre-existing) |
| `factory doctor` prints `manager:` row only when configured | `factory/onboard.py:226-227`; test `tests/test_factory.py:295-303` | Met |
| `dashboard --json` `config` includes table | `factory/dashboard.py:840-844` | Met |
| `templates/factory.toml` documents it | `factory/templates/factory.toml:40-48` | Met |
| Loader test for all three keys | `tests/test_factory.py:112-115`, `:137-138` | Met |
| Out of scope: not running the command | No execution path added; `tests/test_briefing.py:116-124` retains the never-execute check | Met |

## Required fixes

None.

## Optional suggestions (non-blocking)

1. `factory/config.py:288` — `int(manager.get("rounds", ...))` raises `ValueError` (not `ConfigError`) on e.g. `rounds = "x"`, and accepts `rounds = 0`/negative. Matches the existing `gate.timeout`/`dashboard.port` pattern at `:292`, `:305`, so consistent with repo convention; no criterion requires range validation. Leave as-is unless a later ticket consumes `rounds`.
2. `factory/config.py:233-239` — behavior change vs. `manager_model`: `command` is now parsed/validated even when `model` is set explicitly (previously skipped). This is the correct direction for this ticket (command is now a first-class setting) and is covered by `tests/test_factory.py:281-282`; just noting it is a deliberate tightening, not a regression.
3. `factory/templates/factory.toml:41-42` — the reworded model comment is a drive-by edit of adjacent text. Harmless; mentioned only because it is outside the strict diff-to-request trace.

No net-new abstractions introduced; `manager_settings` replaces `manager_model` one-for-one with all callers migrated (`factory/config.py:287`, `tests/test_briefing.py:119-124`).

VERDICT: APPROVE

## Spec (issue #12)

| Criterion | Evidence | Status |
|---|---|---|
| Three keys registered in `KNOWN_KEYS` | `factory/config.py:60` adds `rounds`, `review` to existing `("model", "command")` | OK |
| `Config` + loader for `command` / `rounds=1` / `review="escalated"` | `factory/config.py:104-106` (fields, defaults), `factory/config.py:285-288` (loader) | OK |
| `factory doctor` prints `manager:` row only when configured | `factory/onboard.py:226-227` guarded by `if cfg.manager:` | OK |
| Unknown `[manager]` keys reported as drift | `tests/test_factory.py:252`, `:263` (`manager.unknown` in `.factory.toml keys` detail) — relies on existing `KNOWN_KEYS` drift path, no new code needed | OK |
| `dashboard --json` `config` includes table | `factory/dashboard.py:840-844` | OK |
| `templates/factory.toml` documents it | `factory/templates/factory.toml:43-48` (`{prompt}`/`{cwd}` semantics, defaults, `"all"` meaning) | OK |
| Loader test for all three keys | `tests/test_factory.py:112-115` input, `:137-138` assertions | OK |
| Doctor row present/absent test | `tests/test_factory.py:276-284` | OK |
| `HOST_TABLES` registration | Not in diff. `[manager]` already existed with `model` and README documents `[defaults.manager]` as host-wide, so `[INFERENCE]` it is already a host table; no diff evidence either way | Not citeable |
| Out of scope: running the command | No execution path added | OK |

## Standards (README)

- Loader pattern matches `[review]` handling (`factory/config.py:283-286`): `"command" in table` → `list(...)`; scalar keys via `.get(..., default)` with `int()` coercion like `gate.timeout`. Consistent.
- Doctor row format `manager: <argv0>` mirrors `reviewer:` (`factory/onboard.py:225-227`).

## Non-blocking

- `factory/config.py:288`: `review` accepts any string; issue names only `"escalated"`/`"all"`. Exit gate does not require value validation and drift covers keys not values, so not a REVISE criterion. Noting only.

VERDICT: APPROVE

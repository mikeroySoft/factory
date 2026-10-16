# Review: #12 `[manager]` config table

## Spec (issue #12 exit gate)

| Criterion | Evidence | Status |
|---|---|---|
| `command` / `rounds` / `review` keys in `KNOWN_KEYS`, loader, `Config` | `factory/config.py:60` (`"manager": ("model", "command", "rounds", "review")`), `:104-106` (fields with defaults `None` / `1` / `"escalated"`), `:269`, `:285-288` (loader; `command` unset → `cfg.manager` stays `None` = disabled) | Met |
| `factory doctor` prints `manager:` row only when configured | `factory/onboard.py:226-227` (`if cfg.manager: report(...)`); test `tests/test_factory.py:276-284` covers absent → present | Met |
| Unknown keys under `[manager]` reported as drift | Driven by `KNOWN_KEYS` tuple at `factory/config.py:60`; test `tests/test_factory.py:252`, `:263` (`manager.unknown`) | Met |
| `dashboard --json` `config` includes the table | `factory/dashboard.py:840-844` (`command`, `rounds`, `review`) | Met |
| `templates/factory.toml` documents it | `factory/templates/factory.toml:43-48` (`{prompt}`/`{cwd}` semantics, `command`, `rounds`, `review` with `"all"` note) | Met |
| Loader test for all three keys | `tests/test_factory.py:112-115` (input), `:137-138` (asserts `command`, `rounds=2`, `review="all"`) | Met |
| Out of scope: running the command | No execution path added; template `:43` states it explicitly | Respected |

## Standards (README.md)

- Host-layer pattern: issue says "like `review` (`HOST_TABLES`)". The diff does not touch `HOST_TABLES`; `"manager"` already existed in `KNOWN_KEYS` at base (`factory/config.py:60` context line) and README documents `[defaults.manager]` as host-wide, so `[INFERENCE]` `manager` was already registered in `HOST_TABLES` before this branch. Not citable from the diff; not a finding.
- README `[manager].model` description and template `factory/templates/factory.toml:40-41` remain consistent ("unset: manager.command --model, then OMP default").

## Non-blocking note

- `factory/config.py:288`: `review` accepts any string; `"escalated"`/`"all"` are not validated. The exit gate does not require validation and `[review].command` follows the same unvalidated pattern, so not a REVISE item.

No findings that fail the acceptance criteria or documented conventions.

VERDICT: APPROVE

# Review: issue #12 `[manager]` config table

## Spec coverage

| Criterion | Evidence | Status |
|---|---|---|
| `command` argv template, unset = disabled | `factory/config.py:104` (`manager: list[str] \| None = None`), `:285-286` (set only if `"command" in manager`) | Met |
| `rounds = 1` default | `factory/config.py:105`, `:287` | Met |
| `review = "escalated"` default, `"all"` allowed | `factory/config.py:106`, `:288` | Met (no value validation; see optional) |
| `KNOWN_KEYS` extended | `factory/config.py:60` | Met |
| `HOST_TABLES` registration | Not touched by the diff. `KNOWN_KEYS` already contained `"manager": ("model", "command")` before this change (`config.py:60` context) and README documents `[defaults.manager]` as host-wide, so `[INFERENCE]` the table was already registered. Cannot confirm from the diff alone. | Not contradicted |
| `factory doctor` prints `manager:` row only when configured | `factory/onboard.py:226-227`; test `tests/test_factory.py:276-284` | Met |
| Unknown `[manager]` keys reported as drift | `tests/test_factory.py:252`, `:263` | Met |
| `dashboard --json` `config` includes table | `factory/dashboard.py:840-844` | Met |
| `templates/factory.toml` documents it | `factory/templates/factory.toml:43-48` | Met |
| Loader test for all three keys | `tests/test_factory.py:112-115`, `:137-138` | Met |
| Out of scope: running the command | No execution code added | Met |

Gate: test PASS on target host; not re-evaluated here.

## Required fixes

None.

## Optional suggestions

1. `factory/config.py:288` — `manager_review` accepts any string. Issue text names exactly `"escalated"` and `"all"`; a typo (`"al"`) is silently stored and only surfaces when the (out-of-scope) runner reads it. A one-line check raising/normalising in the loader would match the issue's intent, but the exit gate does not require it and nothing consumes the value yet, so non-blocking.
2. `factory/dashboard.py:841` — `" ".join(cfg.manager)` loses argv boundaries (same as the existing `reviewer`/`workers` rows at `:838-839`, so consistent with the file's convention). Fine as-is.
3. `factory/templates/factory.toml:40-41` — the two reworded `model` comment lines are adjacent-comment edits unrelated to the three new keys; harmless, noting only that they widen the diff beyond the brief.

No unrequested abstractions introduced; new `Config` fields mirror the existing flat `reviewer`/`check_timeout` pattern rather than adding a nested type.

VERDICT: APPROVE

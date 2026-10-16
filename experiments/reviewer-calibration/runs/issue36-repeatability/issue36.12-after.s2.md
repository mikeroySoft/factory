# Review: issue #12 `[manager]` config table (head 7cd7a83)

## Spec coverage

| Criterion (issue #12 / brief) | Evidence | Status |
|---|---|---|
| `command` argv template, unset = disabled | `factory/config.py:104` (`manager: list[str] \| None = None`), `:287` | Met |
| `rounds` default 1 | `factory/config.py:105`, `:288` | Met |
| `review` default `"escalated"`, `"all"` allowed, others rejected | `factory/config.py:106`, `:289-291`; test `tests/test_factory.py:286` | Met |
| Registered in `KNOWN_KEYS` | `factory/config.py:60` | Met |
| `HOST_TABLES` registration | Not in diff. `[INFERENCE]` `manager` was already a host table at base: README documents `[defaults.manager]` host-wide and `tests/test_factory.py:279-281` loads `[defaults.manager]` from the host file successfully. Base state not in packet; cannot confirm from the diff. | Unverifiable, not a defect in the diff |
| `factory doctor` prints `manager:` row only when configured | `factory/onboard.py:226-227`; test `tests/test_factory.py:294-302` | Met |
| Unknown keys under `[manager]` reported as drift | `tests/test_factory.py:252`, `:263` | Met |
| `dashboard --json` `config` includes table | `factory/dashboard.py:840-844` | Met |
| `templates/factory.toml` documents it | `factory/templates/factory.toml:40-48` | Met |
| Loader test for all three keys | `tests/test_factory.py:112-115`, `:137-138` | Met |
| Out of scope: running the command | No execution path added; `tests/test_briefing.py:116-124` retains the never-execute guard | Met |

## Required fixes

None.

## Optional suggestions

1. `factory/config.py:288` — `int(manager.get("rounds", ...))` raises `ValueError` (not `ConfigError`) for `rounds = "x"` and accepts `0`/negative. Matches the existing `check_timeout`/`dashboard_port` pattern at `:292`/`:305`, so consistent with repo convention; a `ConfigError` with a `≥ 1` bound would make the loader's error surface uniform with the `review` check at `:290-291`. Not required by any criterion.
2. `factory/config.py:229-238` — `command = []` passes validation and yields `cfg.manager = []` rather than `None`. Downstream `if cfg.manager:` at `factory/onboard.py:226` and `factory/dashboard.py:841` both treat it as disabled, so no observable defect; normalizing to `None` would make the "unset = disabled" invariant hold on the field itself.
3. `factory/config.py:228-238` — behavior change: `command` is now parsed/validated even when `model` is set (previously skipped at old `:225`). Correct given `command` is now a consumed key; worth a note in the commit message since a host with `model` set plus a malformed legacy `command` string will now fail to load where it previously did not.
4. Confirm `HOST_TABLES` contains `"manager"` at base (see table above). If it does not, the "host settings committed" drift row would not flag a committed `[manager]` block; the issue's Touches list names `HOST_TABLES`. One-line check outside this packet.

No net-new abstractions introduced; `manager_model` → `manager_settings` is a rename/extension of an existing helper, not a new layer.

VERDICT: APPROVE

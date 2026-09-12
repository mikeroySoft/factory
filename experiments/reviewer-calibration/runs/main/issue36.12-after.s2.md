## Review: #12 `[manager]` config table

### Spec coverage

| Criterion | Evidence | Status |
|---|---|---|
| `command` argv template, unset = disabled | `factory/config.py:104` (`manager: list[str] \| None = None`), `:287` (`cfg.manager, cfg.manager_model = manager_settings(manager)`); string form normalized via existing shlex path `:229-238` | Met |
| `rounds = 1` default | `factory/config.py:105`, `:288` | Met |
| `review = "escalated"`, `"all"` allowed, others rejected | `factory/config.py:106`, `:289-291` | Met |
| Host-layer table like `review` | `[defaults.manager]` layering exercised in `tests/test_factory.py:279-282`; README already documents `[defaults.manager]` as host-wide. `HOST_TABLES` not touched in diff — [INFERENCE] `manager` already registered at base, consistent with the pre-existing `"manager": ("model", "command")` entry at `config.py:60` | Met |
| `KNOWN_KEYS` extended / unknown keys → drift | `factory/config.py:60`; `tests/test_factory.py:252`, `:263` | Met |
| `factory doctor` prints `manager:` row only when configured | `factory/onboard.py:226-227`; `tests/test_factory.py:293-301` | Met |
| `dashboard --json` `config` includes table | `factory/dashboard.py:840-844` | Met |
| `templates/factory.toml` documents it | `factory/templates/factory.toml:40-48` | Met |
| Loader test for all three keys | `tests/test_factory.py:112-115`, `:137-138` | Met |
| Out of scope: running the command | No execution path added; `test_briefing.py:116-124` keeps the never-execute guard | Met |

### Standards

- `manager_settings` replaces `manager_model` with a clean cutover: sole caller migrated (`config.py:287`, old call removed `:305`), test renamed (`test_briefing.py:116`). No alias left behind.
- Loader follows the existing per-table pattern (`raw.get(...)`, `int(...)` coercion, `ConfigError` on invalid values) at `config.py:271`, `:287-291`.
- Dashboard `manager.command` serialized as `" ".join(...)` mirroring `reviewer` at `dashboard.py:839`.
- No new abstraction introduced; `manager_settings` is the minimal widening of an existing function (returns argv alongside the model instead of discarding it).

### Required fixes

None.

### Optional suggestions

1. `factory/config.py:288` — `int(manager.get("rounds", ...))` accepts `0` or negatives and raises `ValueError` (not `ConfigError`) on non-numeric input. Matches the existing `gate.timeout` / `dashboard.port` handling at `:292`, `:305`, so consistent with repo convention; a `rounds >= 1` check would be cheap but the issue does not require it.
2. `factory/config.py:287` — an explicit `command = []` yields `cfg.manager == []`, which `onboard.py:226` and `dashboard.py:841` both treat as disabled. Behavior is correct; a comment or normalization to `None` would make the "unset = disabled" contract more legible for the future feature that runs the command.
3. `factory/templates/factory.toml:40-41` — two pre-existing comment lines were reworded alongside the new block. Harmless, but outside the strict scope of the request.

VERDICT: APPROVE

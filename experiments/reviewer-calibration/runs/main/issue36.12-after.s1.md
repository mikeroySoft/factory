# Review: #12 `[manager]` config table (7cd7a83 vs 6ec3546)

## Spec coverage

| Criterion | Evidence | Status |
|---|---|---|
| `command` argv template, unset = disabled | `factory/config.py:104` (`manager: list[str] \| None = None`), `factory/config.py:287` | ✓ |
| `rounds = 1` default | `factory/config.py:105`, `factory/config.py:288` | ✓ |
| `review = "escalated"`, `"all"` allowed, others rejected | `factory/config.py:106`, `factory/config.py:289-291` | ✓ |
| Unknown `[manager]` keys → drift | `factory/config.py:60` (KNOWN_KEYS tuple extended); `tests/test_factory.py:252,263` | ✓ |
| `doctor` prints `manager:` row only when configured | `factory/onboard.py:226-227`; `tests/test_factory.py:294-302` | ✓ |
| `dashboard --json` `config` includes table | `factory/dashboard.py:840-844` | ✓ |
| Template documents all three keys | `factory/templates/factory.toml:43-48` | ✓ |
| Loader test for all three keys | `tests/test_factory.py:112-115,137-138` | ✓ |
| Command never executed | `manager_settings` only parses; `tests/test_briefing.py:116-124` | ✓ |
| Host-layer (`HOST_TABLES`) | Not touched in diff. `[INFERENCE]` `manager` was already in `HOST_TABLES` pre-diff (KNOWN_KEYS already listed it at `factory/config.py:60` old side; README documents `[defaults.manager]`). Host-layer load is exercised by `tests/test_factory.py:279-282` via `host_file('[defaults.manager]…')` and passes on the gate. | ✓ |

## Standards

README states `config.py` is "the single source of every repo-specific value" — honored. Dashboard `manager.command` rendering (`" ".join(...)`, `factory/dashboard.py:841`) mirrors existing `reviewer` at `factory/dashboard.py:839`. Doctor row mirrors `reviewer` at `factory/onboard.py:225`. Renaming `manager_model` → `manager_settings` (`factory/config.py:224`) is a clean cutover: the only other caller (`factory/config.py:305` old) is removed and the briefing test migrated. No net-new abstraction introduced.

## Required fixes

None.

## Optional suggestions

- `factory/config.py:288` — `int(manager.get("rounds", …))` raises `ValueError` (not `ConfigError`) on a non-numeric value, and accepts `0`/negative. This matches the existing `gate.timeout` / `dashboard.port` pattern (`factory/config.py:292,305`), so it is consistent, not a defect; only mention because `review` gets explicit validation two lines below while `rounds` does not.
- `factory/config.py:233,240` — `command = []` yields `cfg.manager == []`, which doctor/dashboard treat as disabled (falsy). Reasonable; noting so the behavior is intentional rather than accidental.
- `tests/test_factory.py:298` — `rows = lambda: …  # noqa: E731`; a nested `def` would avoid the noqa. Style only.

VERDICT: APPROVE

## Standards

Repo has no `AGENTS.md`/`CONTRIBUTING.md`; conventions come from `README.md` and `docs/manager-plan.md` plus existing patterns in `factory/config.py`.

No violations found.

- Host-layer handling: `manager` was already in `HOST_TABLES` on main (`factory/config.py:50`); the diff only extends `KNOWN_KEYS` (`factory/config.py:60`), so `host_filter` / `unknown_keys` / `onboard.py:131,140,149` all pick up `rounds`/`review` with no further wiring. Matches the "like `review`" instruction.
- Loader style matches siblings: `int(manager.get("rounds", …))` (`factory/config.py:288`) mirrors `gate.timeout`/`dashboard.port`; enum check for `review` (`factory/config.py:290-291`) raises `ConfigError` like other validators.
- Doctor row uses the existing `report(shutil.which(argv[0]) …)` shape (`factory/onboard.py:226-227`), dashboard entry uses the same `" ".join(argv)` rendering as `reviewer`/`workers` (`factory/dashboard.py:840-844`).
- Template comment block retains the "dashboard never executes manager.command" contract documented in `README.md:170-174` (`factory/templates/factory.toml:39-48`).
- Smell baseline: nothing notable. `manager_settings` returning a tuple is the smallest change that keeps the single parse of `command` for both the briefing model selector and the new argv; no new abstraction introduced.

## Spec (issue #12 + agent brief)

| Criterion | Status | Evidence |
|---|---|---|
| `command` argv template, unset = disabled | Met | `factory/config.py:105` default `None`; `:229-238` normalizes list/str; doctor/dashboard treat falsy as disabled (`onboard.py:226`, `dashboard.py:841`) |
| `rounds = 1` default | Met | `factory/config.py:106,288` |
| `review = "escalated"`, `"all"` allowed, others rejected | Met | `factory/config.py:107,289-291`; test `tests/test_factory.py:287` |
| Host-layer table like `review` | Met | `HOST_TABLES` already had it (`config.py:50`); `test_factory.py:276-282` loads `[defaults.manager]` from host file |
| `factory doctor` `manager:` row only when configured | Met | `onboard.py:226-227`; test `test_factory.py:293-303` |
| Unknown `[manager]` keys reported as drift | Met | `KNOWN_KEYS` update `config.py:60`; test `test_factory.py:252,263` |
| `dashboard --json` `config` includes it | Met | `dashboard.py:840-844` |
| `templates/factory.toml` documents it | Met | `factory/templates/factory.toml:43-48` |
| Loader test for all three keys | Met | `test_factory.py:112-115,137-138` |
| Out of scope: running the command | Respected | nothing invokes `cfg.manager`; `test_briefing.py:116-124` still asserts non-execution |

No scope creep. Legacy string-form `command` parsing (`config.py:232-236`) predates this branch (README.md:171-172 documents it) and is preserved, not added.

### Changed signature: `manager_model(table) -> str | None` → `manager_settings(table) -> tuple[list[str] | None, str | None]`

Callers outside the diff:
- `factory/briefing.py:345-346,430` — consume `cfg.manager_model` only; still populated at `config.py:287`. Holds.
- No other references (grep: only `config.py:287`, `tests/test_briefing.py:119-124`, both updated).

### Changed `Config` defaults/fields

New fields `manager`, `manager_rounds`, `manager_review` have no pre-existing consumers; `manager_model` default and semantics unchanged.

## Required fixes

None.

## Optional suggestions

- `factory/config.py:288` — `rounds = "x"` raises bare `ValueError` instead of `ConfigError`. Same behavior as `gate.timeout`/`dashboard.port`, so not a rule breach; mention only because T4 will read it.
- `factory/templates/factory.toml:39` — the `[manager]` block now serves two features (briefing `model` vs. frontier `command`); the comments explain it adequately, no change needed.

VERDICT: APPROVE

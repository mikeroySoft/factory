## Standards

No `AGENTS.md`/`CONTRIBUTING.md` in the snapshot; `README.md` and `docs/manager-plan.md` are the only documented sources. Diff follows the existing loader/doctor/dashboard patterns (`review.command` → `list(...)`, `report(shutil.which(...))`, `" ".join(argv)` in `snapshot()`), and `KNOWN_KEYS`/`unknown_keys` drift reporting works unchanged (`review.diff:10`, `:122-131`). No smell-baseline hits worth raising.

## Spec (issue #12 + agent brief)

| Criterion | Evidence | Status |
|---|---|---|
| `[manager]` keys `command`/`rounds`/`review`, defaults unset/1/`"escalated"` | `review.diff:18-20`, `:36-39` | met |
| Host-layer table in `HOST_TABLES` | already present pre-diff: `snapshot/factory/config.py:50` | met (no change needed) |
| `doctor` prints `manager:` row only when configured | `review.diff:67-68`, test `:139-147` | met |
| Unknown `[manager]` keys reported as drift | `review.diff:10`, test `:123,131` | met |
| `dashboard --json` `config` includes table | `review.diff:51-55` | met |
| `templates/factory.toml` documents it | `review.diff:85-90` | met |
| Loader test for all three keys | `review.diff:102-105`, `:113-114` | met |
| Out of scope: running the command | nothing executes it | met |

### Required fixes

1. **`manager.command` loader bypasses/contradicts the existing validator** — `review.diff:36-37` (`cfg.manager = list(manager["command"])`) runs before `cfg.manager_model = manager_model(raw.get("manager", {}))` (`snapshot/factory/config.py:302`), and `manager_model` explicitly accepts a command *string* via `shlex.split` and rejects anything else with `ConfigError("manager.command must be an argv array or command string")` (`snapshot/factory/config.py:231-237`).
   - Trigger A: `command = "omp -p --model x {prompt}"` (accepted by the validator) → `cfg.manager == ['o','m','p',' ',...]`; `doctor` reports `manager: o` FAIL (`review.diff:68`) and the dashboard `config.manager.command` shows `o m p   - p …` (`review.diff:52`). Config loads with no error and both new surfaces the issue asks for report garbage.
   - Trigger B: `command = 5` / `command = true` → `list()` raises a bare `TypeError` traceback at `review.diff:37` instead of the `ConfigError` the existing validator would have produced one line later.
   - Fix (smallest): reorder so `manager_model(...)` runs before `review.diff:36`, and either reuse its `shlex.split` branch for the string form or make `manager_model` reject strings so both paths agree. Defect class: concrete correctness (silent wrong output + unhandled exception on a value the repo's own validator defines as valid/invalid).

### Optional suggestions

- `review.diff:39`: `manager.review` is spec'd as `"escalated"` with `"all"` allowed; the loader accepts any string. A typo (`"al"`) silently behaves as `"escalated"` when T6 consumes it. A one-line `if cfg.manager_review not in ("escalated", "all"): raise ConfigError(...)` would match the `gate.check` name validation style at `snapshot/factory/config.py:295-296`. Not a documented rule; non-blocking.

## Plan drift

Plan status: not recorded; using the issue "Touches" list as the plan.

- Files changed not named: `tests/test_factory.py` — implied by the exit gate ("passes with a loader test for all three keys"); not drift of substance.
- Named but untouched: `HOST_TABLES` — already contains `"manager"` at `snapshot/factory/config.py:50`; no edit required.
- Planned proof vs gate report: `uv run pytest tests/test_factory.py` ↔ `test: PASS`. The doctor-row and drift criteria are covered by the added tests (`review.diff:131`, `:145-147`), so no planned proof is missing. Drift exposes no defect.

VERDICT: REVISE

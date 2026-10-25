## Standards

Sources: `README.md` (config section, lines 118–119: template documents all keys), `factory/templates/factory.toml` comment convention, `pyproject.toml` ruff `line-length = 100`, existing loader patterns in `factory/config.py`.

- `factory/config.py:10` adds `rounds`, `review` to `KNOWN_KEYS["manager"]`; `HOST_TABLES` already contained `manager` pre-diff (snapshot `config.py:50`), so host layering works without a diff change. Consistent with `review` table handling.
- `factory/config.py:70-74` loader follows the existing `int(table.get(key, default))` / `ConfigError` pattern (`max_active`, `check_timeout`). `review` enum validated with `ConfigError`; `manager = 5` (non-table) caught by `manager_settings` at line 32-33 before `.get("rounds")` runs at line 71.
- `factory/dashboard.py:94-98` mirrors the `reviewer`/`workers` `" ".join(argv)` shape.
- `factory/onboard.py:110-111` mirrors the `reviewer` `shutil.which` row.
- `factory/templates/factory.toml:125-133` documents all three keys with the same comment style as `[workers]`/`[review]`; README defers to the template (README:118-119), so no README change needed.
- `tests/test_factory.py:229` lambda + `# noqa: E731` matches existing usage (`test_factory.py:234,310` in snapshot).
- No smell-baseline issues of note. Mild Divergent Change: `manager_settings` now serves both the dashboard briefing model selector and the frontier manager argv (`config.py:30-54`); acceptable, both read the same table.

## Spec (issue #12 + agent brief)

| Criterion | Evidence | Status |
|---|---|---|
| `command` argv template, unset = disabled | `config.py:18` (`manager: list[str] \| None = None`), `config.py:36-44` normalizes list or string; `onboard.py:110` treats unset as disabled | met |
| `rounds = 1` default | `config.py:19,71` | met |
| `review = "escalated"`, `"all"` allowed | `config.py:20,72-74` | met |
| Host-layer like `review` | `HOST_TABLES` pre-existing; host-layer loading proven at `tests/test_factory.py:209-212` | met |
| `factory doctor` prints `manager:` row when configured, nothing otherwise | `onboard.py:110-111`; test `tests/test_factory.py:224-232` | met |
| Unknown keys under `[manager]` reported as drift | `KNOWN_KEYS` at `config.py:10`; test `tests/test_factory.py:190,198` | met |
| `dashboard --json` `config` includes table | `dashboard.py:94-98` inside `snapshot()` which feeds `--json` | met |
| `templates/factory.toml` documents it | `factory.toml:128-133` | met |
| Loader test for all three keys | `tests/test_factory.py:169-172,180-181` | met |
| Out of scope: running the command | No execution path added; `test_briefing.py:146-152` still asserts no execution | met |

No scope creep found. `manager_settings` returning the argv (`config.py:30,54`) is the minimal way to expose `command`; not a net-new abstraction.

## Plan drift

Plan (issue "Touches" / `docs/manager-plan.md:240-241`) names `config.py`, `onboard.py`, `templates/factory.toml`, `dashboard.py`.

Files changed not named by the plan:
- `tests/test_factory.py` — required by the exit gate ("loader test for all three keys"); not drift in substance.
- `tests/test_briefing.py:145-157` — forced by the `manager_model` → `manager_settings` rename (`config.py:28-30`). Rename is a worker choice, not a plan item; no behavioral change (same assertions).

Planned proof vs. gate report: plan's proof is `uv run pytest tests/test_factory.py` and the doctor row behavior. Gate report shows `test: PASS`; the doctor behavior is asserted inside the test suite (`tests/test_factory.py:224-232`). Nothing planned is missing from the gate. No drift exposes a defect.

## Required fixes

None.

## Optional suggestions

- `config.py:28-30,54`: renaming `manager_model` → `manager_settings` and changing the return to a tuple rippled into `tests/test_briefing.py:146-157`. Keeping `manager_model` as-is and normalizing `command` inline in `load()` would be a smaller diff with one fewer touched file. Non-blocking.
- `config.py:71`: `int(manager.get("rounds", ...))` raises a bare `ValueError` traceback on `rounds = "x"`, same as the existing `max_active`/`check_timeout` lines. Consistent with repo convention; mention only.

VERDICT: APPROVE

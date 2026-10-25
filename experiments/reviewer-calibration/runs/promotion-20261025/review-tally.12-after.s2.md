```json
{"verdict":"APPROVE","findings":[{"severity":"nit","pass":"bug","path":"factory/config.py","line":71,"summary":"`rounds` is coerced with bare `int()` — a non-numeric or negative value (`rounds = \"x\"`, `rounds = -1`) escapes as ValueError or is accepted silently, unlike `review` which raises ConfigError. Mirrors the existing `gate.timeout` pattern so not a rule breach; worth a `< 1` ConfigError before T4 consumes it."},{"severity":"nit","pass":"spec","path":"tests/test_factory.py","line":229,"summary":"Line is ~131 chars vs `[tool.ruff] line-length = 100` (pyproject.toml:34). E501 is not in ruff's default select and pre-existing lines (config.py:270) also exceed it, so not enforced; splitting the lambda into a helper def would also drop the `noqa: E731`."}]}
```

**Standards.** No AGENTS.md/CONTRIBUTING.md in snapshot; only `pyproject.toml` ruff config and `skills/factory/SKILL.md` (operational, not code-style). `HOST_TABLES` already contained `"manager"` (config.py:50, unchanged), so the loader's host-layer handling matches `review`. Validation raises `ConfigError` consistent with neighbours; `manager_settings` is ordered before `manager.get(...)` so a non-table `[manager]` fails with ConfigError rather than AttributeError (config.py:287–291).

**Spec (issue #12 + brief).**
- `command` argv template, unset = disabled: `Config.manager: list[str] | None = None`, doctor row only `if cfg.manager` (review.diff:110–111), dashboard `command: None` when unset (review.diff:95). ✓
- `rounds = 1`, `review = "escalated"` / `"all"`: defaults review.diff:19–20; `review` validated review.diff:73–74. ✓
- Unknown `[manager]` keys as drift: `KNOWN_KEYS` review.diff:10; asserted review.diff:190,198. ✓
- `factory doctor` row when configured / nothing when not: review.diff:110–111, test review.diff:224–232. ✓
- `dashboard --json` `config` includes table: review.diff:94–98. ✓
- Template documents it: review.diff:128–133. ✓
- Loader test for all three keys: review.diff:169–172,180–181. ✓
- Out of scope "running the command": no subprocess added; `manager_settings` docstring/test confirm never-execute (review.diff:31,146–152). ✓

**Plan drift.** Issue "Touches" names `config.py`, `onboard.py`, `templates/factory.toml`, `dashboard.py`. Also changed: `tests/test_factory.py` (required by the exit gate's loader test) and `tests/test_briefing.py` (forced by the `manager_model` → `manager_settings` rename; no other callers remain in snapshot). Neither exposes a defect. Gate report lists `test: PASS` without naming `uv run pytest tests/test_factory.py`; the doctor row and drift exit-gate conditions are covered by tests inside that suite, so no planned proof is unaccounted for.

VERDICT: APPROVE

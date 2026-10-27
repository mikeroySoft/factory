**Spec trace (issue #12 brief, all paths verified against snapshot):**
- `command` argv template, unset = disabled: `config.py:18` (`manager: list[str] | None = None`), `config.py:70`; legacy string form shlex-split (`config.py:37-44`). ✓
- `rounds = 1` default, `review = "escalated"` with `"all"` allowed and validated: `config.py:19-20`, `71-74`. ✓
- Host-layer table: `HOST_TABLES` already contained `"manager"` pre-diff (snapshot `config.py:50`); `host_filter` + `merge` layer `[defaults.manager]` (snapshot `config.py:267-268`), exercised by `test_factory.py:206-212`. ✓
- `KNOWN_KEYS` extended (`config.py:10`) → `unknown_keys` reports `manager.unknown` as drift; `test_factory.py:190,198`. ✓
- `factory doctor` row only when configured: `onboard.py:110-111`; `test_factory.py:224-232`. ✓
- `dashboard --json` `config.manager`: `dashboard.py:94-98`. ✓
- Template documents all three keys: `factory.toml:128-133`. ✓
- Loader test for all three keys: `test_factory.py:169-172,180-181`. ✓
- Running the command: not implemented anywhere (`manager_settings` never executes; `test_briefing.py:146-152` guards). ✓

**Standards:** `manager_model` → `manager_settings` rename has no remaining stale callers (grep over snapshot). Config validation raises `ConfigError` for table/command/review per the existing loader pattern. Ruff config only sets line-length (no E501 selected); long test lines match 41 pre-existing ones in `test_factory.py`.

**Plan drift:** Touches named `config.py`, `onboard.py`, `templates/factory.toml`, `dashboard.py`. Diff additionally changes `tests/test_factory.py` (required by the exit gate's loader test) and `tests/test_briefing.py` (forced by the `manager_model` rename; no other path to keep the no-execute guard test). Neither exposes a defect. Exit-gate proof (`pytest tests/test_factory.py`) is covered by the gate report's `test: PASS`. No worker plan recorded, so no further comparison possible.

```json
{"verdict":"APPROVE","findings":[{"severity":"nit","pass":"bug","path":"factory/config.py","line":71,"summary":"`int(manager.get(\"rounds\", …))` accepts 0/negative and raises ValueError (not ConfigError) on non-numeric input; matches the existing `gate.timeout` pattern at config.py:75, so not a rule violation, but a `rounds < 1` guard would make T4's `round <= manager.rounds` loop meaningful."},{"severity":"nit","pass":"spec","path":"factory/config.py","line":45,"summary":"`command = []` or `command = \"\"` yields `cfg.manager == []`, which doctor/dashboard treat as disabled (falsy) — consistent, but `dashboard --json` reports `null` while the loader stored `[]`; harmless today."}]}
```

VERDICT: APPROVE

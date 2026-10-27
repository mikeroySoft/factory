Issue #12 scope (fetched via API, with the triage brief comment): `[manager] command/rounds/review`, host-layer table (`manager` already in `HOST_TABLES` at base, `snapshot/factory/config.py:50`), doctor row only when configured, unknown-key drift, `dashboard --json` `config` entry, template docs, loader test for all three keys, no execution.

**Standards** — No AGENTS.md/CONTRIBUTING.md in the snapshot; `skills/factory/SKILL.md:24` says the `factory init` template documents every key, which the diff does (`review.diff:128-133`). Patterns match existing code: `int(table.get(...))` loader style, `" ".join(argv)` in dashboard, `report(shutil.which(...))` in doctor.

**Spec** — All exit-gate items covered:
- Three keys loaded (`review.diff:70-74`), loader test (`review.diff:169-181`).
- Doctor row only when configured (`review.diff:110-111`, test `review.diff:224-232`).
- Unknown `[manager]` keys drift via `KNOWN_KEYS` (`review.diff:10`, test `review.diff:190,198`).
- Dashboard config (`review.diff:94-98`).
- `review` validated to `escalated|all` (`review.diff:73-74`). Command never executed; `manager_settings` only normalizes (`review.diff:30-54`, test `review.diff:146-157`).

**Plan drift** — Plan not recorded; against the issue's "Touches": `tests/test_briefing.py` and `tests/test_factory.py` are extra. `test_factory.py` is required by the exit gate; `test_briefing.py` follows the `manager_model`→`manager_settings` rename (single caller remaining at `config.py:287`). Planned proof (`uv run pytest tests/test_factory.py`) is covered by gate `test: PASS`. No defect exposed.

```json
{"verdict":"APPROVE","findings":[{"severity":"nit","pass":"bug","path":"factory/config.py","line":71,"summary":"`rounds` is coerced with int() only: non-numeric values raise ValueError (not ConfigError) and 0/negative are accepted; matches existing timeout/port handling, so no rule violated."}]}
```
VERDICT: APPROVE

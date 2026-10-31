I found no Important defects. The diff meets every acceptance criterion and exit-gate item in issue #12, so it can be approved. I reviewed the diff and snapshot only; per the brief, I ran no builds or tests.

**Standards.** The snapshot has no AGENTS.md or CONTRIBUTING.md, so the conventions come from `docs/manager-plan.md` (task T3) and the README. The diff copies the existing `review` pattern:
- **Config:** `manager` is added to `KNOWN_KEYS` (`factory/config.py:10`). It was already in `HOST_TABLES` before this diff, so that file needed no change there.
- **Dashboard:** the command is shown as a space-joined string, the same way `reviewer` is (`factory/dashboard.py:94-98`).
- **Doctor:** the `manager:` row follows the same `shutil.which` check as the reviewer row (`factory/onboard.py:110-111`).
- **New abstractions:** none. `manager_model` was renamed to `manager_settings` and now also returns the argv, so one function parses the table.

**Spec (issue #12 and the agent brief):**
- **`command`:** an argv template that may contain `{prompt}` and `{cwd}`. If unset, `cfg.manager` stays `None`, which means disabled (`factory/config.py:18,36-45`).
- **`rounds`:** defaults to 1 (`factory/config.py:19,71`).
- **`review`:** defaults to `"escalated"`, accepts `"all"`, and anything else raises `ConfigError` (`factory/config.py:20,72-74`).
- **Doctor:** prints a `manager:` row only when the command is set (`factory/onboard.py:110-111`), and a test checks both cases (`tests/test_factory.py:224-232`).
- **Drift:** an unknown key under `[manager]` is reported (`tests/test_factory.py:190,198`).
- **Dashboard and template:** `dashboard --json` `config` includes the table (`factory/dashboard.py:94-98`), and `templates/factory.toml` documents all three keys (`factory/templates/factory.toml:128-133`).
- **Loader test:** one test covers all three keys (`tests/test_factory.py:169-172,180-181`).
- **Out of scope respected:** nothing runs the command. The test that proves the command is never executed was kept (`tests/test_briefing.py:146-152`).

**Plan drift.** No worker plan was recorded. Against the issue's list of files to touch:
- `tests/test_briefing.py` was not named. It had to change because of the rename (`tests/test_briefing.py:145-157`), so it exposes no defect.
- `tests/test_factory.py` was not named in the touch list, but the exit gate requires it.
- The gate report shows `test: PASS` but does not name `uv run pytest tests/test_factory.py`. I treat the test line as covering it, since the report is authoritative for test status.

**Nit:** `rounds` is converted with a plain `int()` and never checked. A non-numeric value fails with a bare `ValueError` instead of `ConfigError`, and 0, negative numbers or `true` are accepted silently. `review` in the same table is validated.

```json
{"verdict":"APPROVE","findings":[{"id":"F1","severity":"nit","pass":"bug","class":"unvalidated-manager-rounds","path":"factory/config.py","line":71,"summary":"manager.rounds goes through bare int() with no check: a non-numeric value raises an uncaught ValueError instead of ConfigError, and 0, negative values and booleans are accepted silently, while manager.review in the same table is validated (lines 73-74).","instances":[{"path":"factory/config.py","line":71,"summary":"cfg.manager_rounds = int(manager.get(\"rounds\", cfg.manager_rounds)) has no type or range check"}]}]}
```
VERDICT: APPROVE

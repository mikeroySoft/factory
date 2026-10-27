```json
{"verdict":"APPROVE","findings":[{"id":"F1","severity":"nit","pass":"bug","class":"unvalidated-int-setting","path":"factory/config.py","line":288,"summary":"`manager.rounds` is only `int()`-cast: a non-numeric value surfaces as a bare ValueError instead of ConfigError, and 0/negative values are accepted silently. Matches the existing `check_timeout`/`dashboard_port` pattern, so not a rule violation; the T4 runner (`round <= manager.rounds`) tolerates it.","instances":[{"path":"factory/config.py","line":288,"summary":"`int(manager.get(\"rounds\", ...))` without ConfigError wrapping or lower bound"}]}]}
```

Spec check against issue #12 / agent brief: `command` (argv or legacy string, unset → `None`), `rounds` (default 1), `review` (`escalated`|`all`, validated) loaded at `factory/config.py:287-291`; `manager` already in `HOST_TABLES` (`config.py:50`) and `KNOWN_KEYS` extended (`config.py:60`) so `[manager].unknown` is reported as drift (`tests/test_factory.py:198`); doctor prints `manager:` only when configured (`factory/onboard.py:226-227`, test at `tests/test_factory.py:224-232`); `dashboard --json` `config.manager` (`factory/dashboard.py:840-844`); template documents all three keys (`factory/templates/factory.toml:43-48`); loader test covers all three keys (`tests/test_factory.py:137-138`). Command is never executed (`tests/test_briefing.py:116-124`). Gate test PASS covers the issue's `uv run pytest tests/test_factory.py` proof.

Plan drift: no plan recorded. Files outside the issue's "Touches" list: `tests/test_briefing.py` (forced by the `manager_model`→`manager_settings` rename; all callers migrated) and `tests/test_factory.py` (required by exit gate). No missing proof.

VERDICT: APPROVE

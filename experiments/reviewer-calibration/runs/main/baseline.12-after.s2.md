## Standards

No convention violations found. Loader additions follow the existing pattern (`int(table.get(...))`, `ConfigError` on invalid shape), `factory/config.py:287-291`; doctor row uses the same `report(shutil.which(...))` shape as `reviewer`, `factory/onboard.py:226-227`; snapshot `config` entry mirrors `workers`/`reviewer`, `factory/dashboard.py:840-844`.

## Spec (issue #12)

| Criterion | Evidence |
|---|---|
| `command`/`rounds`/`review` keys registered | `factory/config.py:60` |
| `command` unset = disabled | `manager_settings` returns `None` when absent, `factory/config.py:228-229`; doctor gated on `cfg.manager`, `factory/onboard.py:226` |
| `rounds` default 1, `review` default `"escalated"`, `"all"` allowed | `factory/config.py:104-106`, `:290-291` |
| doctor prints `manager:` only when configured | `factory/onboard.py:226-227`; test `tests/test_factory.py:294-302` |
| unknown `[manager]` keys → drift | `tests/test_factory.py:252`, `:263` |
| `dashboard --json` `config` includes table | `factory/dashboard.py:840-844` |
| template documents it | `factory/templates/factory.toml:43-48` |
| loader test for all three keys | `tests/test_factory.py:112-115`, `:137-138` |
| command never executed (out of scope preserved) | `tests/test_briefing.py:116-124` |

Note, not a finding: `HOST_TABLES` is named in the issue but does not appear in the diff; whether `manager` was already present there is not visible in this packet. Host-layer loading is exercised by `tests/test_factory.py:276-281` (`[defaults.manager]` host file → `cfg.manager` populated), which the gate reports passing.

VERDICT: APPROVE

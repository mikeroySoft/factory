## Spec (issue #12)

| Criterion | Evidence | Status |
|---|---|---|
| `command` argv template, unset = disabled | `factory/config.py:104`, `:285-286` (`cfg.manager` stays `None` unless `command` present) | met |
| `rounds` default 1 | `factory/config.py:105`, `:287` | met |
| `review` default `"escalated"`, `"all"` allowed | `factory/config.py:106`, `:288`; test `tests/test_factory.py:115`, `:138` exercises `"all"` | met |
| `KNOWN_KEYS` lists all three keys | `factory/config.py:60` | met |
| Unknown `[manager]` keys reported as drift | `tests/test_factory.py:252`, `:263` asserts `manager.unknown` in the keys WARN detail | met |
| `factory doctor` prints `manager:` row only when configured | `factory/onboard.py:226-227`; `tests/test_factory.py:276-284` covers both absent and present | met |
| `dashboard --json` `config` includes table | `factory/dashboard.py:840-844` | met |
| `templates/factory.toml` documents it | `factory/templates/factory.toml:43-48` | met |
| Loader test for all three keys | `tests/test_factory.py:112-115`, `:137-138` | met |
| Running the command out of scope | no execution path added | met |

**Unverifiable from the diff:** the issue says "register in `HOST_TABLES`". The diff does not touch `HOST_TABLES`; `factory/config.py:60` shows `"manager"` already existed in `KNOWN_KEYS` at base (with `model`, `command`), and README's "host-wide: `[defaults.manager]`" indicates the table was already host-layer before this change. `[INFERENCE]` No change needed; the new test at `tests/test_factory.py:252` does not assert `manager` in the "host settings committed" row, so host-layer membership is not proven by the diff. Not a failing finding.

## Standards (README)

No documented convention violated. Template comment style matches surrounding `[review]`/`[gate]` entries (`factory/templates/factory.toml:43-48`); test additions follow existing `HostConfigTest` stub pattern.

VERDICT: APPROVE

# Review: #12 `[manager]` config table (4830b7d)

## Spec coverage

| Criterion | Evidence | Status |
|---|---|---|
| `KNOWN_KEYS` lists `command`, `rounds`, `review` | `factory/config.py:60` | met |
| `Config` fields: `command` unset = disabled, `rounds=1`, `review="escalated"` | `factory/config.py:104-106` | met |
| Loader reads all three | `factory/config.py:269`, `:285-288` | met |
| `doctor` prints `manager:` row only when configured | `factory/onboard.py:226-227`; test `tests/test_factory.py:276-284` | met |
| Unknown `[manager]` keys reported as drift | `tests/test_factory.py:252`, `:263` (relies on existing `KNOWN_KEYS` drift path) | met |
| `dashboard --json` `config.manager` | `factory/dashboard.py:840-844` | met |
| Template documents the keys | `factory/templates/factory.toml:43-48` | met |
| Loader test for all three keys | `tests/test_factory.py:112-115`, `:137-138` | met |
| Out of scope: running the command | nothing in diff executes `cfg.manager` | respected |

## Standards

Only README.md exists as a convention source; diff matches existing loader/doctor/snapshot patterns (mirrors `review.command` handling at `factory/config.py:283-284`, `factory/onboard.py:225`). No convention violations.

## Required fixes

None.

## Optional suggestions

1. `factory/config.py:288` — `review` accepts any string; the issue names only `"escalated"` and `"all"`. A typo (`"al"`) silently loads. Not blocking: the issue specifies defaults/allowed values but no rejection rule, and no existing loader key validates enums (e.g. triage `model`). If you want fail-fast, a one-line `ValueError` in the loader suffices; no abstraction warranted.
2. `HOST_TABLES` — issue "Touches" names it, but the diff does not modify it. `"manager"` was already in `KNOWN_KEYS` at base (`factory/config.py:60` context line), so it is plausibly already in `HOST_TABLES` `[INFERENCE]`; the packet doesn't inline that constant. Worth a glance that `manager` is listed there so `rounds`/`review` resolve from `[defaults.manager]` like `command`.
3. `factory/config.py:286` — `command = []` loads as `[]`, which is falsy and treated as disabled by `onboard.py:226` and `dashboard.py:841`. Consistent, just noting the implicit equivalence of empty list and unset.

VERDICT: APPROVE

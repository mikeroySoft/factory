## Standards

No AGENTS.md/CONTRIBUTING.md at this head; README is the only convention source. The diff follows the existing loader/doctor/snapshot patterns exactly (`factory/config.py:286-288` mirrors the `review.command` block above it; `factory/onboard.py:226-227` mirrors the reviewer row; `factory/dashboard.py:840-844` mirrors `reviewer`). Template comment style matches surrounding `[manager]` block (`factory/templates/factory.toml:43-48`).

## Spec (issue #12)

| Criterion | Evidence | Status |
|---|---|---|
| `command` argv template, unset = disabled | `factory/config.py:104`, `:286-287` (`None` unless key present) | Met |
| `rounds = 1` default | `factory/config.py:105`, `:288` | Met |
| `review = "escalated"` default | `factory/config.py:106`, `:289` | Met |
| `KNOWN_KEYS` registers three keys | `factory/config.py:60` | Met |
| `HOST_TABLES` membership | Not in diff. `manager` was already present in `KNOWN_KEYS` at base (`factory/config.py:60` context) and README documents `[defaults.manager]` as host-wide, so `[INFERENCE]` it is already in `HOST_TABLES`; no diff line contradicts. | Not verifiable from diff; no defect observed |
| doctor `manager:` row only when configured | `factory/onboard.py:226-227`; test `tests/test_factory.py:276-284` | Met |
| unknown keys under `[manager]` → drift | `tests/test_factory.py:252`, `:263` | Met |
| `dashboard --json` `config` includes table | `factory/dashboard.py:840-844` | Met |
| `templates/factory.toml` documents it | `factory/templates/factory.toml:43-48` | Met |
| loader test for all three keys | `tests/test_factory.py:112-115`, `:137-138` | Met |
| Out of scope: running the command | Nothing executes `cfg.manager` | Met |

## Required fixes

None.

## Optional suggestions

- `factory/config.py:289` accepts any string for `review`; issue names only `"escalated"`/`"all"`. Issue does not demand validation and no other loader key validates enum values, so not blocking. If desired later, a one-line `ValueError` on anything else is sufficient; no abstraction warranted.
- `tests/test_factory.py:282` asserts `assertNotIn("manager: manage", rows())` — fine, but asserting no key starting with `"manager:"` would also catch a mislabelled row. Minor.

VERDICT: APPROVE

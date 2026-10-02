## Spec (issue #12)

| Criterion | Evidence | Status |
|---|---|---|
| `command` argv template, unset = disabled | `factory/config.py:104` (`manager: list[str] \| None = None`), `factory/config.py:285-286` | Met |
| `rounds = 1` default | `factory/config.py:105`, `factory/config.py:287` | Met |
| `review = "escalated"` default | `factory/config.py:106`, `factory/config.py:288` | Met |
| `KNOWN_KEYS` registration | `factory/config.py:60` adds `rounds`, `review` | Met |
| Doctor `manager:` row only when configured | `factory/onboard.py:226-227`; test `tests/test_factory.py:276-284` | Met |
| Unknown `[manager]` keys reported as drift | test `tests/test_factory.py:252`, `tests/test_factory.py:263` | Met |
| `dashboard --json` `config.manager` | `factory/dashboard.py:840-844` | Met |
| Template documents all three keys | `factory/templates/factory.toml:43-48` | Met |
| Loader test for all three keys | `tests/test_factory.py:112-115`, `tests/test_factory.py:137-138` | Met |
| Out of scope: running the command | No execution path added | Met |

Non-blocking observations:

- `factory/config.py:288` — `manager_review` accepts any string; issue says `"escalated"` default, `"all"` allowed. No validation or drift report for other values (e.g. `review = "al"`). Not in the exit gate; not a REVISE trigger.
- `HOST_TABLES` is named in the issue's Touches but the diff does not modify it. `KNOWN_KEYS` already had a `manager` entry at base (`factory/config.py:60` context line) and README documents `[defaults.manager]` as host-wide, so `[INFERENCE]` `manager` is already in `HOST_TABLES` at `origin/main`. Cannot confirm from the diff alone.

## Standards (README)

- Loader follows the existing `review.command` pattern (`factory/config.py:283-286`), dashboard snapshot mirrors `reviewer` serialization (`factory/dashboard.py:839-844`), doctor mirrors the reviewer row (`factory/onboard.py:225-227`). No second convention introduced.
- No AGENTS.md/CONTRIBUTING.md at this head; nothing further to check.

VERDICT: APPROVE

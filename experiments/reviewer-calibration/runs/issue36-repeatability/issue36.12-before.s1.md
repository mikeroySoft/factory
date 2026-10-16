## Spec compliance (issue #12)

| Criterion | Evidence | Status |
|---|---|---|
| `[manager]` keys `command`, `rounds`, `review` registered in `KNOWN_KEYS` | `factory/config.py:60` | met |
| `Config` fields + loader: `command` unset → disabled (`None`), `rounds` default 1, `review` default `"escalated"` | `factory/config.py:104-106`, `:285-288` | met |
| `factory doctor` prints `manager:` row only when configured | `factory/onboard.py:226-227`; test `tests/test_factory.py:276-284` asserts absence then PASS | met |
| Unknown keys under `[manager]` reported as drift | `tests/test_factory.py:252`, `:263` (`manager.unknown` in `.factory.toml keys` detail) | met |
| `dashboard --json` `config` includes the table | `factory/dashboard.py:840-844` | met |
| `templates/factory.toml` documents it, incl. `{prompt}`/`{cwd}` and `"all"` | `factory/templates/factory.toml:43-48` | met |
| Loader test for all three keys | `tests/test_factory.py:112-115`, `:137-138` | met |
| Out of scope: running the command | no execution path added | met |
| Test suite passes | gate report: `test: PASS` | met |

`HOST_TABLES` is not touched by the diff. `manager` already existed in `KNOWN_KEYS` at the base (`factory/config.py:60` context line `-"manager": ("model", "command")`) and README documents `[defaults.manager]` as host-wide, so `[INFERENCE]` it is already registered in `HOST_TABLES`; the diff provides no evidence either way. Not blocking: nothing in the diff removes or contradicts host-layer handling, and the drift test exercises the key table.

## Standards

Only README.md exists as documented convention at this head. Diff follows the existing `review`/`reviewer` pattern (list argv, `shutil.which(argv[0])` doctor row, `" ".join` in snapshot) rather than introducing a new abstraction. No net-new abstractions.

## Required fixes

None.

## Optional suggestions

- `factory/config.py:288` — `manager_review` accepts any string; issue only names `"escalated"`/`"all"`. Since running the command is out of scope and the issue does not require rejection, leaving it unvalidated is acceptable; a typo will surface only when the runner lands. Not a rule violation.
- `tests/test_factory.py:281` — lambda-assigned helper with `noqa: E731`; a nested `def` avoids the suppression. Style only.

VERDICT: APPROVE

## Standards

No AGENTS.md/CONTRIBUTING.md at this head; README is the only convention source. Diff matches the existing `[review]` loader pattern (`factory/config.py:285-288` mirrors `:283-284`), existing doctor `report(shutil.which(...))` pattern (`factory/onboard.py:226-227`), and the template's comment-documented defaults (`factory/templates/factory.toml:43-48`). No convention violations found.

## Spec (issue #12 exit gate)

| Criterion | Evidence | Status |
|---|---|---|
| `command`/`rounds`/`review` keys, defaults `1`/`"escalated"`, unset command = disabled | `factory/config.py:104-106`, `:285-288` | Met |
| Unknown keys under `[manager]` reported as drift | `factory/config.py:60` (KNOWN_KEYS tuple), `tests/test_factory.py:252`, `:263` | Met |
| `factory doctor` prints `manager:` row only when configured | `factory/onboard.py:226-227`; `tests/test_factory.py:276-285` asserts both absent and present | Met |
| `dashboard --json` `config` includes table | `factory/dashboard.py:840-844` | Met |
| `templates/factory.toml` documents it | `factory/templates/factory.toml:43-48` | Met |
| Loader test for all three keys | `tests/test_factory.py:112-115`, `:137-138` | Met |
| Out of scope: running the command | Nothing executes `cfg.manager` | Met |

## Required fixes

None.

## Optional suggestions

- `factory/config.py:288` — `review` accepts any string; issue allows only `"escalated"`/`"all"`. Nothing in the diff validates or consumes the value, so a typo (`"esclated"`) silently loads. Not blocking: the issue does not state rejection of other values, and the consumer is out of scope. If validated, a one-line check in the loader suffices; no abstraction needed.
- `HOST_TABLES` is not touched by the diff. `[INFERENCE]` `"manager"` is already registered there, since `manager.command`/`manager.model` pre-existed at base (`factory/config.py:60` old line, README `[defaults.manager]`). Cannot confirm from the diff alone; if it is absent, `[manager]` would not surface in the `host settings committed` drift row. `tests/test_factory.py:265` (`assertIn("triage, gate.lock", ...)`) is a substring match and would not catch either case.

VERDICT: APPROVE

# Review: issue #20 — `when` rules, FIX decision, ci-fix/conflict profiles

## Spec coverage

| Requirement | Evidence | Status |
|---|---|---|
| `[workers.<label>]` table with `command` + optional `when`; array form still loads | `factory/config.py:288-298` handles both `dict` and list entries; `tests/test_factory.py:147-165` covers mixed array/table plus rejection cases | ✓ |
| Manager prompt lists labels with `when` | `factory/manage.py:199-201` emits `- <label>: <when or "(no when rule)">` for non-reserved labels | ✓ |
| ROUTE/FIX only for listed labels | `factory/manage.py:54`, `:63-66`; parse receives the filtered `workers` at `:213` | ✓ |
| ROUTE chore without worker → rejected + recorded; with worker → label applied | `tests/test_factory.py:652-675` asserts `manage` event `HUMAN` vs `ROUTE` and `--add-label chore` | ✓ |
| FIX on red CI runs the selected argv | `factory/manage.py:104-131` dispatches `worker_round` with `{data["worker"]}`; `tests/test_factory.py:677-717` verifies `fix-worker` ran, guidance + packet reached it, push gated on gate PASS, `factory-approved` only on APPROVE | ✓ |
| `ci-fix` / `conflict` profiles in template | `factory/templates/factory.toml:31-47` | ✓ (see note) |
| Touches `dashboard.py` | `factory/dashboard.py:847` exposes `worker_when` | ✓ |
| Manager cannot write host config | Nothing in diff writes config; README `README.md:100-101` states it | ✓ |

## Notes (non-blocking)

- Profiles ship commented-out (`factory/templates/factory.toml:41-47`) rather than as active entries. Consistent with the issue's "remain host-config, human-applied" and the triage comment; the README wording (`README.md:100`, "opt-in … for a human to apply in host config") matches. Not a defect under the stated acceptance criteria.
- `factory/manage.py:54` still subtracts `RESERVED_LABELS` even though the caller now passes a pre-filtered dict (`:199`, `:213`). Redundant, harmless; keeps `parse` safe for other callers.
- `factory/manage.py:108` compares `headRefName` to `agent/{n}` after `gh pr view agent/{n}` already selected by that branch. Redundant guard, no behavioral risk.
- The template comment directs users to `[defaults.workers.<label>]` / `[repo."owner/name".workers.<label>]` (`factory/templates/factory.toml:37-38`). The host-config merge path is not in the diff, so I cannot confirm table-form entries survive that layer `[INFERENCE]`; the gate's passing test suite is the only evidence and it covers repo-level `.factory.toml` only (`tests/test_factory.py:147-165`).

## Standards

No AGENTS.md/CONTRIBUTING.md at this head. README is updated in step with behavior (`README.md:88-101`), and the decision menu (`factory/manage.py:19-29`) matches the documented decision set. No convention violations found.

VERDICT: APPROVE

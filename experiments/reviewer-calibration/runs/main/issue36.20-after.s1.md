# Review: issue #20 — when-rules, FIX decision, ci-fix/conflict profiles

## Spec coverage

| Acceptance criterion | Evidence | Status |
|---|---|---|
| `[workers.<label>]` table with optional `when`; array form still loads | `factory/config.py:289-298` handles dict-or-list per label; `tests/test_factory.py` `test_worker_tables_and_legacy_arrays` covers both forms and five malformed entries | Met |
| Manager prompt lists labels with `when` | `factory/manage.py:199-201` builds `- {label}: {when or '(no when rule)'}` from non-reserved workers | Met |
| ROUTE/FIX only for listed labels | `factory/manage.py:54` (ROUTE) and `:63-66` (FIX) validate against `workers`; `manage_pass` passes the filtered dict at `:213` | Met |
| ROUTE chore without worker rejected and recorded | parse falls through to `HUMAN` + `manage` event recorded at `:220` before `apply`; `test_route_uses_only_listed_worker_labels_and_when_rules` asserts `HUMAN` decision and no `issue edit` | Met |
| FIX on red CI runs `ci-fix` argv | `factory/manage.py:117-120` dispatches `worker_round` with `{data["worker"]}`; test asserts worker ran, guidance+packet reached it, push only on gate PASS, `factory-approved` only on fresh APPROVE | Met |
| Two profiles in `templates/factory.toml` | `factory/templates/factory.toml:41-47` | Met (see optional note) |
| Touches `dashboard.py` | `factory/dashboard.py:847` exposes `worker_when` | Met |
| Out of scope: manager writing host config | No config writes in diff | Respected |

## Standards

No AGENTS.md/CONTRIBUTING.md at this head. README conventions: labels/`agent/<n>` scheme unchanged; `manage` event recorded before GitHub mutation (`manage.py:220` precedes `apply`); README updated for the new decision and worker form (`README.md:88-102`). Human veto respected: `manage.py:109` refuses FIX when `reviewDecision == "CHANGES_REQUESTED"`, consistent with README step 6.

## Required fixes

None.

## Optional suggestions

1. `factory/templates/factory.toml:41-47` — profiles are shipped commented out with instructions to copy into host config. The issue says "ship two default profiles" and "remain host-config, human-applied"; the commented form satisfies the second clause but a reader could expect active defaults. If the intent was active-by-default in the repo template, uncomment; otherwise current form is defensible. Not blocking — the brief is ambiguous and the test suite does not pin either reading.
2. `factory/manage.py:54`, `:65` — `parse` re-subtracts `RESERVED_LABELS` even though `manage_pass` now passes the pre-filtered dict (`:199`, `:213`). Harmless redundancy; keeps `parse` safe for other callers. Leave as is unless you want a single filter point.
3. `factory/config.py:296` — stores the TOML list object directly where the old code did `list(v)`. No observed consumer mutates `cfg.workers[label]`, so no defect; mention only because the copy was dropped silently.
4. `factory/manage.py:116` — manager-produced `guidance` is spliced into the worker prompt ahead of the packet. The packet is already untrusted content per the MENU, so this is not a new trust boundary, but the worker prompt could label the guidance as manager-originated/untrusted the same way the packet is.

VERDICT: APPROVE

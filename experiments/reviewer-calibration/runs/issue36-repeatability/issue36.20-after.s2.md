# Review: #20 — when-rules, ci-fix/conflict profiles (ce5568e)

## Spec coverage

| Criterion | Evidence | Status |
|---|---|---|
| `[workers.<label>]` table with optional `when`; array form still loads | `factory/config.py:289-298`; `tests/test_factory.py` `test_worker_tables_and_legacy_arrays` | Met |
| Manager prompt lists labels with `when` | `factory/manage.py:199-201` | Met |
| ROUTE/FIX only for listed labels | `factory/manage.py:54`, `:63-66`, `:213` (pre-filtered `workers` passed to `parse`) | Met |
| ROUTE chore rejected+recorded without worker; applied with one | `test_route_uses_only_listed_worker_labels_and_when_rules` — HUMAN decision recorded via `dispatch.record` at `manage.py:220` before `apply` | Met |
| FIX on red CI runs `ci-fix` argv | `factory/manage.py:104-131`; `test_fix_runs_selected_worker_on_red_ci_and_requires_gate_and_review` | Met |
| ci-fix / conflict profiles in template | `factory/templates/factory.toml:41-47` | Met (see Optional 1) |
| `dashboard.py` touched | `factory/dashboard.py:847` | Met |

Standards: no AGENTS.md/CONTRIBUTING.md at this head. README updated in-diff (`README.md:93-102`) consistent with the implemented behaviour. No net-new abstraction beyond the brief; `RESERVED_LABELS` (`manage.py:32`) replaces an inline set literal already used at `:54`, now shared with `:65` — justified by reuse, not speculative.

## Required fixes

None.

## Optional suggestions

1. **Profiles are commented out, not active defaults** — `factory/templates/factory.toml:41-47`. Issue says "Ship two default profiles"; the brief and issue both also say "remain host-config, human-applied", and the template is repo config (`.factory.toml`), so shipping them inert with copy-to-host instructions (`:37-39`) is a defensible reading. Flagging only because the issue author may have intended uncommented entries; confirm with the author rather than change.

2. **Possible FIX re-trigger loop on gate failure** — `factory/manage.py:121-123`, `:129-130`. On gate FAIL or REVISE the code calls `dispatch.escalate` on a ticket already in `ready-for-human`. `[INFERENCE]` If `escalate` writes a fresh escalation packet, the next `manage` pass treats it as a new escalation and `[manager].rounds` restarts, so a manager that keeps answering FIX could burn worker budget repeatedly. `escalate`'s packet/round semantics are outside the diff; if rounds are keyed per-packet, consider counting FIX attempts per ticket or passing a flag so manage skips packets produced by a FIX. Not blocking without evidence of the loop.

3. **Human-takeover window during FIX** — `factory/manage.py:217-221`. `human_activity` is checked before `apply`; FIX then runs a worker round up to `budget_min`, during which a human may request changes or relabel. `reviewDecision` is checked only at `:109` before the round. A re-check of `human_activity`/`reviewDecision` before the push at `:124` would close the window. Same pattern pre-exists for the other decisions, so optional.

4. **`worker_when` not surfaced in dashboard UI** — `factory/dashboard.py:847` adds it to the JSON snapshot only. Issue lists `dashboard.py` under Touches without an acceptance criterion; snapshot exposure satisfies the text.

VERDICT: APPROVE

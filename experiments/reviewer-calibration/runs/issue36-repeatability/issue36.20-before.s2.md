# Review: issue #20 — `when` rules, FIX decision, ci-fix/conflict profiles

## Spec coverage (issue #20 + brief)

| Criterion | Evidence | Status |
|---|---|---|
| `[workers.<label>]` table with optional `when`; array form still loads | `factory/config.py:288-298`; `tests/test_factory.py:147-165` covers both forms and five malformed entries | Met |
| Manager prompt lists labels with `when` | `factory/manage.py:199-201` (`- {k}: {when or '(no when rule)'}`) | Met |
| ROUTE/FIX only for listed labels | `factory/manage.py:53-57` (ROUTE), `:63-69` (FIX, also rejects reserved); `parse` now receives the reserved-filtered map `:213` | Met |
| ROUTE chore without worker → rejected + recorded; with worker → label applied | `tests/test_factory.py:652-675` asserts `manage` event `HUMAN` vs `ROUTE`, no `issue edit` vs `--add-label chore` | Met |
| FIX on red CI runs `ci-fix` argv | `factory/manage.py:104-130`; `tests/test_factory.py:677-717` checks worker ran in `wt-7`, guidance + packet in prompt, push only on gate PASS, label only on fresh APPROVE | Met |
| Two default profiles in template, human-applied | `factory/templates/factory.toml:31-47` (commented, with copy instructions) | Met — consistent with "remain host-config, human-applied" |
| Touches `dashboard.py` | `factory/dashboard.py:847` exposes `worker_when` | Met |
| Out of scope: manager writing host config | No config writes in diff; template comment states it `:34-35` | Respected |

## Standards

README is the only convention doc at this head. The diff updates README `:88-102` to document FIX semantics, table-form workers, and the profiles; matches the implemented behavior (`manage.py:104-130`). No new abstractions beyond `RESERVED_LABELS` (`manage.py:32`), which replaces a duplicated literal set at `:54` and is reused at `:66`/`:199` — justified by the three callsites.

## Required fixes

None.

## Optional suggestions

1. `factory/manage.py:110` — `git branch --show-current` is empty during an in-progress rebase (detached HEAD), so a `FIX {"worker":"conflict"}` on a worktree left mid-rebase is rejected with "not on the ticket branch". The template's `conflict` command (`factory.toml:47`) assumes the rebase may have been aborted; whether the dispatcher aborts on conflict is outside the diff `[INFERENCE]`. If it does not, the shipped `conflict` profile cannot be dispatched via FIX. Worth confirming against `dispatch.py`'s rebase path; not blocking since the exit gate only names the `ci-fix` path.
2. `factory/manage.py:121,129` — `dispatch.escalate` after a failed FIX gate/review presumably writes a fresh escalation packet, which `manage_pass` would pick up next pass and could FIX again until `[manager].rounds` is exhausted `[INFERENCE]`. Bounded by rounds, so acceptable; README `:96-97` ("failure stays with the human") is accurate only within that bound.
3. `factory/manage.py:54` — `set(workers) - RESERVED_LABELS` is now redundant since `workers` arrives pre-filtered (`:199`, `:213`); harmless defense-in-depth, leave or drop.

VERDICT: APPROVE

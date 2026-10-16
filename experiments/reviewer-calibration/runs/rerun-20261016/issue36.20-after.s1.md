## Review: issue #20 — when-rules, ci-fix/conflict profiles

### Spec coverage (issue #20 + brief)

| Criterion | Evidence | Status |
|---|---|---|
| `[workers.<label>]` table with `command` + optional `when`; array form still loads | `factory/config.py:288-298` (table-or-array per entry, validates argv and `when` type); `tests/test_factory.py:147-165` covers both forms and malformed entries | Met |
| Manager prompt lists labels with their `when` | `factory/manage.py:199-201` (`- {label}: {when or '(no when rule)'}`), asserted at `tests/test_factory.py:672` | Met |
| ROUTE/FIX accepted only for listed labels | `factory/manage.py:54`, `:63-66`; `RESERVED_LABELS` at `:34`; unconfigured `ROUTE chore` → HUMAN, no `issue edit` (`tests/test_factory.py:669-676`); FIX rejects `ci-fix` (unconfigured), `default`, `ready-for-agent`, non-string (`:738-748`) | Met |
| `ROUTE chore` with worker applies label | `tests/test_factory.py:673` (`--add-label chore`) | Met |
| FIX on red CI runs `ci-fix` argv, re-gates, pushes only on PASS, re-reviews, APPROVE restores label | `factory/manage.py:104-131`; test matrix `tests/test_factory.py:678-736` checks worktree mutation, guidance+packet in worker input, push only when gate_ok, `factory-approved` only on gate_ok ∧ APPROVE, no requeue | Met |
| Ship `ci-fix` and `conflict` profiles in template; profiles remain host-config, human-applied | `factory/templates/factory.toml:31-47` (commented, with copy-to-host-config instructions) | Met — commented-out is consistent with "remain host-config": an active entry in `.factory.toml` would be repo config, contradicting the issue text |
| Decision recorded before mutation | `factory/manage.py:219` unchanged; FIX precondition failures raise `ValueError` → caught at `:223` as `mechanism_failure` | Met |
| Out of scope: manager writing host config | No config writes in diff | Met |

### Standards (README at head)
- README updated for the new decision and worker forms (`README.md:88-102`); MENU text matches (`factory/manage.py:19-29`).
- FIX honours documented human veto (`reviewDecision == CHANGES_REQUESTED` rejects, `factory/manage.py:109`) and the `agent/<n>` branch convention (`:111`).
- Force-push uses `--force-with-lease` (`:124`), matching the merge-stage refresh behaviour described in README step 6.

### Required fixes
None.

### Optional suggestions (non-blocking)
1. `factory/manage.py:54` — `set(workers) - RESERVED_LABELS` is now redundant for the `manage_pass` caller, which already filters at `:199`. Harmless; keeps `parse` safe for direct callers. Leave or drop — not a rule.
2. `factory/config.py:297-298` — `when` on a reserved label (e.g. `[workers.default]`) is stored in `worker_when` but never surfaced to the prompt. Not a defect; a `ConfigError` or silent drop would both be fine. No action required.
3. `factory/templates/factory.toml:37-38` — the comment directs users to `[defaults.workers.<label>]` / `[repo."owner/name".workers.<label>]`. `[INFERENCE]` host-config merging into `raw` before `config.py:284` is outside this diff; the gate's passing tests only exercise repo-level `.factory.toml`. If host-layer parsing does not route through the same `workers` branch, the documented copy path would not pick up `when`. Worth a one-line smoke on the target host; not citable as a defect from this diff.

No unrequested abstractions introduced; `RESERVED_LABELS` consolidates an existing inline set used by two branches, justified by the new FIX validation at `:65`.

VERDICT: APPROVE

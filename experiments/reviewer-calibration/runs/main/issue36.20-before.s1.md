# Review: issue #20 — when-rules, FIX decision, ci-fix/conflict profiles

## Standards

No AGENTS.md / CONTRIBUTING.md at this head; README is the only documented convention source. The diff updates README's manager section (`README.md:88-102`) to match the new `FIX` decision and table-form workers, so docs and behavior stay in sync. No convention violations found.

## Spec (issue #20 + brief)

| Criterion | Evidence | Status |
|---|---|---|
| `[workers.<label>]` table with `command` + optional `when` | `factory/config.py:288-298` | Met |
| Legacy array `[workers]` still loads | `factory/config.py:290` (`entry` non-dict path); `tests/test_factory.py:147-158` | Met |
| Manager prompt lists labels with `when` | `factory/manage.py:199-201` | Met |
| ROUTE/FIX only accept listed labels | `factory/manage.py:53-56` (ROUTE), `:63-66` (FIX); `parse` receives reserved-filtered `workers` at `:213` | Met |
| ROUTE chore without chore worker → rejected and recorded | `parse` falls through to `HUMAN` (`manage.py:72`); `dispatch.record("manage", ...)` at `:219` runs before `apply`; `tests/test_factory.py:652-676` | Met |
| ROUTE chore with chore worker → label applied | existing ROUTE apply path; asserted at `tests/test_factory.py:673` | Met |
| FIX on red CI runs `ci-fix` argv | `manage.py:104-131`: `worker_round(n, wt, {data["worker"]}, ...)` selects by the single label; `tests/test_factory.py:678-720` asserts the stub worker ran and `--add-label factory-approved` only on gate PASS + APPROVE | Met |
| `ci-fix` and `conflict` profiles in `templates/factory.toml` | `factory/templates/factory.toml:31-47` | Met (see optional note 1) |
| Touches config/manage/dashboard/template | all four touched; `dashboard.py:847` exposes `worker_when` | Met |
| Out of scope: manager writing host config | Nothing in diff writes config; template comment states it explicitly (`factory.toml:34-35`) | Met |

## Required fixes

None.

## Optional suggestions (non-blocking)

1. **Profiles shipped commented out** — `factory/templates/factory.toml:41-47`. The issue says "ship two default profiles" while also saying "remain host-config, human-applied"; the commented-out form satisfies the second clause and avoids creating labels `factory init` doesn't know about. Consistent with the brief; noting only that the "ship" wording could be read as live entries. No change requested.

2. **Redundant reserved-label subtraction** — `factory/manage.py:54` still computes `set(workers) - RESERVED_LABELS` even though `manage_pass` now passes a pre-filtered dict (`manage.py:199`, `:213`). Harmless defense for direct `parse` callers; keep or drop at your discretion.

3. **Post-FIX ticket state** — `factory/manage.py:126-131`. On APPROVE the issue keeps `ready-for-human`; README documents "FIX does not merge or requeue" (`README.md:96-97`), so this is intentional. Worth confirming the merge stage's eligibility check doesn't require absence of `ready-for-human`, otherwise a fixed-and-approved PR needs a manual relabel before it lands. Cannot verify from the diff; `[INFERENCE]` only.

4. **FIX escalation on gate failure re-escalates an already-escalated ticket** — `factory/manage.py:120-122`, `:130-131`. This produces a fresh packet and comment, which is reasonable forensics, but with `[manager].rounds > 1` it re-enters the manager loop with the new packet. Bounded by `rounds`; documented behavior ("failure stays with the human"). No change requested.

## Abstractions

`RESERVED_LABELS` (`manage.py:32`) replaces an inline set literal that was about to be duplicated for FIX — justified by the second use site. `worker_when` as a parallel dict rather than a richer worker record (`config.py:103`) is the smaller change and keeps `cfg.workers: dict[str, list[str]]` stable for existing callers (`cfg.worker`, dashboard, stats). No unrequested abstractions.

VERDICT: APPROVE

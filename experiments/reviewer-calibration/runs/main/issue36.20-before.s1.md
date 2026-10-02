# Review: issue #20 — when-rules, FIX decision, ci-fix/conflict profiles

## Spec coverage

| Criterion | Evidence | Status |
|---|---|---|
| `[workers.<label>]` table with `command` + optional `when`; array form still loads | `factory/config.py:289-298`; `tests/test_factory.py` `test_worker_tables_and_legacy_arrays` | met |
| Prompt lists labels with `when` | `factory/manage.py:199-201` | met |
| ROUTE/FIX only for listed labels | `factory/manage.py:54`, `:63-66`, `:213` (parse receives filtered `workers`) | met |
| ROUTE chore rejected+recorded without worker, label applied with one | `tests/test_factory.py` `test_route_uses_only_listed_worker_labels_and_when_rules` | met |
| FIX on red CI runs `ci-fix` argv | `factory/manage.py:104-131`; `test_fix_runs_selected_worker_on_red_ci_and_requires_gate_and_review` | met |
| Two profiles in `templates/factory.toml` | `factory/templates/factory.toml:41-47` (commented, human-applied) | met — consistent with "remain host-config, human-applied" |
| README updated | `README.md:88-102` | met |

## Required fixes

**1. Shipped `conflict` profile cannot succeed through the FIX path.** `factory/templates/factory.toml:45-47` ships a worker whose job is to complete a rebase of `agent/<n>` onto `main`. Two FIX-path lines defeat it:

- `factory/manage.py:111-112` — a worktree left mid-rebase (the state README §7 describes as kept "for forensics") has a detached HEAD; `git branch --show-current` prints empty → `ValueError("FIX worktree is not on the ticket branch")` → `mechanism_failure` at `factory/manage.py:224`, round consumed.
- `factory/manage.py:124` — `git push origin agent/{n}` without `--force-with-lease`. A completed rebase rewrites history relative to the remote `agent/<n>` (remote is pre-rebase by definition: the conflict is why the dispatcher's own rebase→force-push in README §6 never ran). Push is non-fast-forward → `CalledProcessError` → `mechanism_failure` after the worker's full budget is spent.

Trigger: `DECISION: FIX {"worker":"conflict",...}` on any rebase-conflict escalation. Impact: the profile the issue requires to ship is unreachable (precondition) or always fails after spending budget (push). Scope: issue #20 "Scope" — `conflict` (resolve the rebase …) is a named deliverable. Fix options: accept a mid-rebase worktree (check `rebase-merge`/`rebase-apply` state or `git rev-parse --abbrev-ref HEAD` after the worker finishes instead of before), and push with `--force-with-lease=agent/<n>` the way the dispatcher's refresh path does. `[INFERENCE]` on whether `dispatch.escalate` leaves the rebase in progress or aborts it — the template's own text (`factory.toml:47` "If the rebase was aborted, restart it") says both states are possible, and both fail as above. No regression test covers this path; `test_fix_runs_selected_worker…` only exercises a fast-forward push against a stubbed `git push`.

## Optional suggestions

- `factory/manage.py:109-112` precondition failures are classified `mechanism_failure` at `:224`. Per README "Outcomes" table that outcome means the configured mechanism could not run; a manager choosing FIX when no open PR / wrong branch exists is a bad decision, closer to `unknown`. Recording already satisfies the "rejected and recorded" gate, so non-blocking.
- `factory/templates/factory.toml:37-38` tells the human to copy `[workers.ci-fix]` under `[defaults.workers.<label>]`, but the snippet headers at `:41`/`:45` are repo-form; a literal copy needs renaming. Whether the host-config merge accepts the table form at all is outside this diff — `[INFERENCE]`, unverified.
- `factory/manage.py:116` embeds the full escalation packet in the worker prompt. Harmless, but the human-activity check at `:217-218` ran before a worker round that can take `budget_min`; a human taking over during FIX is not re-checked before `push`/`approve_pr` at `:124-128`. Same window exists for other decisions, just much shorter.
- `factory/config.py:296` stores the TOML list by reference (`list(v)` copy dropped). No current mutation site in the diff; cosmetic.

No unrequested abstractions introduced; `RESERVED_LABELS` (`factory/manage.py:33`) replaces a duplicated literal set and is used at three sites.

VERDICT: REVISE

## Standards

No `AGENTS.md`/`CONTRIBUTING.md` in the snapshot; the documented conventions are `README.md`, `factory/templates/factory.toml` comments, and `docs/manager-plan.md`. The diff updates README (`review.diff:9-24`) and the template (`review.diff:208-224`) alongside the behavior; `skills/factory/SKILL.md` does not enumerate manager decisions, so nothing missed there. `KNOWN_KEYS["workers"]` is `None` (`snapshot/factory/config.py:58`), so the new nested `command`/`when` keys do not trip `factory doctor` drift.

Baseline smells (judgment calls, non-blocking):
- **Duplicated Code**: `review.diff:159-165` (push → review → `pr_comment` → `approve_pr`/escalate) repeats `snapshot/factory/dispatch.py:1064-1067`.
- **Possible Feature Envy**: the FIX branch in `manage.apply` (`review.diff:139-166`) drives `dispatch.cfg/LOGS/EVENTS/worker_round/review/escalate` directly; the rest of `apply` only issues `gh issue` calls.

## Spec (issue #20 + agent brief)

| Criterion | Status | Evidence |
|---|---|---|
| `[workers.<label>]` may carry `when`; array form still loads | met | `review.diff:45-55`; test `review.diff:236-254` |
| Manager prompt lists labels with their `when` | met | `review.diff:176-178` |
| ROUTE/FIX only accepted for listed labels | met | `review.diff:112-115`, `review.diff:120-123`; `parse` receives the reserved-filtered dict (`review.diff:187`) |
| `ROUTE chore` without a `chore` worker rejected and recorded; with one, label applied | met | `review.diff:263-286` (HUMAN decision recorded via `dispatch.record("manage", …)`, `review.diff:193`) |
| `FIX` on red CI runs the `ci-fix` argv | met | `review.diff:152-155`, test `review.diff:288-330` |
| Ship `ci-fix` and `conflict` profiles in the template, host-config/human-applied | met (commented, opt-in) | `review.diff:218-224`; consistent with the triage brief "these remain host-config that humans apply" |
| Out of scope: manager writing host config | respected | no config writes in diff |
| `dashboard.py` touched | met | `review.diff:67` (exposed in snapshot; not rendered in `dashboard.html`) |

Loop safety: a gate failure after FIX calls `escalate` (`review.diff:157`), which bumps the round (`snapshot/factory/dispatch.py:369-371`); `manage_pass` already refuses rounds `> manager_rounds`, so FIX cannot loop. Ticket lock is shared with `process_ticket`, so FIX and dispatch cannot run the same worktree concurrently.

## Plan drift

Plan status: not recorded, so no planned file list or proof list to diff against. Relative to the issue's "Touches" list, extra files changed: `README.md`, `tests/test_factory.py` — both expected for a behavior change. Gate report `test: PASS` covers the issue's `uv run pytest tests/test_factory.py` criterion [INFERENCE: gate's test command is the suite].

## Required fixes

1. **`review.diff:159` — FIX push cannot land the `conflict` profile's result.** Trigger: the only escalation the shipped `conflict` worker exists for is `refresh_pr_branch`'s rebase conflict, which runs `git rebase --abort` and leaves the worktree at the pushed tip (`snapshot/factory/dispatch.py:722-724`). The worker then re-rebases (`review.diff:224`: "If the rebase was aborted, restart it"), rewriting `agent/<n>` history. `dispatch.run(["git", "push", "origin", f"agent/{n}"])` is then rejected as non-fast-forward → `CalledProcessError` → caught at `review.diff:197-199` as `mechanism_failure` with only a log line: no PR comment, no escalation, no re-review, and the round is consumed so the manager never retries. Impact: issue #20's `conflict` profile ("resolve the rebase, keep both intents") is dead on arrival through FIX, and README's "re-gates, pushes only on gate PASS, and re-reviews" (`review.diff:20-21`) is false for that profile. Fix: push with `--force-with-lease` exactly as the existing rebase path does (`snapshot/factory/dispatch.py:731`); it is a no-op change for the append-only `ci-fix` case.

## Optional suggestions

- `review.diff:112`: `set(workers) - RESERVED_LABELS` is now redundant since `manage_pass` already filters (`review.diff:176`); harmless, keep or drop.
- `review.diff:67`: `worker_when` is in the snapshot but `dashboard.html:893-894` still renders only `cfg.workers`; a one-line tweak would show the rule next to each label. Not required by the issue.
- `review.diff:159-165`: consider reusing the push/review/approve tail from `process_ticket` rather than copying it; only worth it if a third caller appears.
- `review.diff:49`: the validation condition is long enough that splitting into two `if`s would read better at 3am.

VERDICT: REVISE

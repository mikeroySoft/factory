# Review: #20 — when-rules, ci-fix/conflict profiles (8dd6588 vs 07913b4)

## Spec coverage

| Criterion | Evidence | Status |
|---|---|---|
| `[workers.<label>]` table with optional `when`; array form still loads | `factory/config.py:289-298`; `tests/test_factory.py` `test_worker_tables_and_legacy_arrays` | Met |
| Prompt lists labels with `when` | `factory/manage.py:199-201` | Met |
| ROUTE/FIX accept only listed labels | `factory/manage.py:54`, `:63-66`, `:213` | Met |
| `ROUTE chore` without worker rejected + recorded; with worker label applied | `test_route_uses_only_listed_worker_labels_and_when_rules` (HUMAN decision recorded as `manage` event; `--add-label chore` when configured) | Met |
| FIX on red CI runs `ci-fix` argv | `factory/manage.py:117-120`; `test_fix_runs_selected_worker_on_red_ci_and_requires_gate_and_review` | Met |
| `ci-fix` and `conflict` profiles in template, human-applied | `factory/templates/factory.toml:41-47` (commented opt-in blocks) | Met — commented-out form is consistent with "remain host-config, human-applied" |
| Touches limited to named files (+README, tests) | diff | Met |

No AGENTS.md/CONTRIBUTING.md at this head; README conventions (`{prompt}`/`{cwd}` placeholders, `agent/<n>` branch scheme, `manage` event before mutations) are followed.

## Required fixes

**1. `conflict` profile cannot complete through FIX: non-force push after a rebase.**
- `factory/manage.py:124` — `git push origin agent/{n}` with no `--force-with-lease`.
- `factory/templates/factory.toml:46-47` — the shipped profile's job is "Resolve the rebase conflicts … If the rebase was aborted, restart it against the configured main". A completed rebase rewrites `agent/<n>` history; the remote already holds the pre-rebase head (PR is OPEN per the `:107-109` check).
- Trigger: `DECISION: FIX {"worker":"conflict",…}` on a rebase-conflict escalation; worker finishes the rebase; gate passes.
- Impact: push rejected non-fast-forward → `CalledProcessError` → `manage_pass` records `mechanism_failure` (`:221-224`), round consumed, resolved commits stranded in the worktree. The one profile the issue ships for conflicts is unusable via the only decision that can dispatch it. Dispatcher's own refresh path force-pushes after rebase (README "Behind `main` → rebase, re-gate on this host, force-push"), so FIX diverges from the existing convention.
- Secondary, same path: `factory/manage.py:111-112` rejects the worktree if `git branch --show-current` is empty, which is the state of a worktree left mid-rebase (detached HEAD). Whether the dispatcher aborts the rebase before escalating is not in the diff `[INFERENCE]`; if it does not, FIX refuses the `conflict` profile before the worker ever runs. Either accept a mid-rebase worktree or document that the escalation path aborts it.
- Fix: push with `--force-with-lease=agent/{n}` (matches the dispatcher's refresh behaviour); verify/handle the mid-rebase worktree state.

## Optional suggestions

- `factory/templates/factory.toml:43,47` — `{prompt}` is embedded inside a longer argument ("Read the ticket prompt at {prompt}"). Every other example and the test (`["fix-worker", "{prompt}"]`) uses it as a whole argument. Confirm `Config.worker` performs substring replacement; otherwise the profile passes the literal `{prompt}` to `omp`. Not provable from the diff.
- `factory/manage.py:54`, `:65`, `:197` — `RESERVED_LABELS` is subtracted in `manage_pass` and again inside `parse` for both ROUTE and FIX. One site suffices; harmless.
- `factory/config.py:297-298` — a `when` on `default` is accepted and silently never listed (`manage.py:197` filters it). Rejecting or ignoring explicitly would avoid a confusing host config; not a rule.
- `factory/manage.py:114-115` — attempt numbering for FIX reads the whole journal each call; fine at current scale, noted only because the gate did not exercise large journals.

No net-new abstractions beyond the brief; `RESERVED_LABELS` and `worker_when` are the minimum needed for the listed-label rule.

VERDICT: REVISE

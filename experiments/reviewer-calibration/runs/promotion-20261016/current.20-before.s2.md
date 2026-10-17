## Standards

Sources: README.md (manager contract), `docs/manager-plan.md` T11/§4.3, `factory/templates/factory.toml`. No AGENTS.md/CONTRIBUTING.md in repo.

- README rule "records a `manage` event before GitHub mutations" — upheld: `factory/manage.py:219` records before `apply` at `:221`.
- Plan §4.3 "Re-add `factory-approved` … after gate PASS + fresh codex APPROVE" — upheld: `factory/manage.py:121-130` (escalate on gate FAIL, `approve_pr` only on APPROVE).
- Plan T11 "`[workers]` becomes table-or-array" — upheld: `factory/config.py:289-298`; legacy arrays keep loading (`tests/test_factory.py:147-166`).
- Smell, judgement call (**Duplicated Code**): `factory/manage.py:124-130` repeats the push → review → `pr_comment` → approve/escalate shape of `dispatch.process_ticket` (`factory/dispatch.py:1064-1067`). Not blocking; a shared helper is only worth it if a third caller appears.
- Smell, judgement call (**Divergent Change**): `apply()` now starts with a 30-line FIX branch (`factory/manage.py:104-131`) that dispatches workers, while the rest of the function does `gh issue` edits. Not blocking.

No documented-standard violations.

## Spec

Issue #20 / agent brief, item by item:

- `when` on `[workers.<label>]`, validated — `factory/config.py:290-298` ✓
- Prompt lists labels with `when` — `factory/manage.py:199-201` ✓ (`tests/test_factory.py:672`)
- ROUTE/FIX accepted only for listed labels — `factory/manage.py:53-56`, `:63-66`; `manage_pass` passes the filtered map at `:213` ✓ (`tests/test_factory.py:652-676`, `:722-732`)
- `ci-fix` and `conflict` profiles in template, human-applied, commented out — `factory/templates/factory.toml:41-47` ✓
- Exit gate: ROUTE chore rejected+recorded / applied — `tests/test_factory.py:668-676` ✓; FIX on red CI runs the `ci-fix` argv — `tests/test_factory.py:678-717` ✓; array-form `[workers]` loads ✓
- Dashboard: `factory/dashboard.py:847` exposes `worker_when`; `dashboard.html` does not render it. Issue only names `dashboard.py` — acceptable.

### Required fixes

1. **FIX cannot land the shipped `conflict` profile** — `factory/manage.py:124` pushes with plain `git push origin agent/<n>`.
   Trigger: `refresh_pr_branch` escalates "rebase onto moved main conflicts" after `git rebase --abort` (`factory/dispatch.py:722-725`); manager answers `FIX {"worker":"conflict"}`; the profile text at `factory/templates/factory.toml:47` instructs the worker to restart and resolve the rebase, so the worktree's `agent/<n>` is rewritten; gate passes; the non-fast-forward push is rejected → `CalledProcessError` → caught at `factory/manage.py:222-226` as `mechanism_failure`.
   Impact: no review, no `factory-approved`, no escalation, no issue comment; the manage round is consumed (`:189-192`), so with the default `manager.rounds = 1` the ticket is dead for the manager, and the human sees only a dispatcher log line. One of the two profiles the issue requires ("ship two default profiles … `conflict` (resolve the rebase …)") is therefore unusable through the only path that dispatches it (plan §4.3 line 142). The repo's existing rebase path pushes with `--force-with-lease` (`factory/dispatch.py:731`); FIX must do the same — and the README claim at `README.md:94-96` ("pushes only on gate PASS") remains true.

### Optional suggestions

- `factory/manage.py:108-112`: precondition failures (no open PR, wrong branch, `CHANGES_REQUESTED`) raise `ValueError` → only a log line; consider recording/commenting so the human learns why the round was spent. Same gap as the `CalledProcessError` case above, lower frequency.
- `factory/manage.py:107-121`: a FIX worker round can run up to `budget_min`; unlike ROUTE/SPLIT there is no `human_activity` re-check between the worker finishing and the push/approve at `:124-128`.
- `factory/manage.py:54`: `set(workers) - RESERVED_LABELS` re-filters a map already filtered at `:199`; harmless, could drop one.
- `factory/templates/factory.toml:37-39`: the "keep a default worker in that layer" note is load-bearing — a host `[defaults.workers.ci-fix]` with no `default` makes every repo lacking its own `[workers]` fail at `factory/config.py:286-287`. Pre-existing semantics, but worth stating the failure mode explicitly in the comment.

Summary — Standards: 0 violations, 2 judgement-call smells. Spec: 1 required (conflict profile unlandable via plain push), 4 optional.

VERDICT: REVISE

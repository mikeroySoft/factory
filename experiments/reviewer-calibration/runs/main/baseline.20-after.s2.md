## Spec (issue #20)

- `when` on `[workers.<label>]`, table-or-array coexistence, validation: `factory/config.py:288-298`. Array form still loads (`tests/test_factory.py:148-157` mixes `default = [...]` with `[workers.chore]` table; `tests/test_factory.py:687` uses array `chore = ["false"]` beside a table `ci-fix`).
- Prompt lists routable labels with their `when`: `factory/manage.py:199-201`; reserved labels excluded via `RESERVED_LABELS` (`factory/manage.py:33`).
- ROUTE/FIX accept only listed labels: `parse` now receives the filtered map (`factory/manage.py:213`); FIX validation at `factory/manage.py:63-69`. Exit gate "ROUTE chore without a chore worker is rejected and recorded" covered at `tests/test_factory.py:670-676` (decision `HUMAN` in `manage` event, no `issue edit`); with a worker, `--add-label chore` at `tests/test_factory.py:674-675`.
- FIX on red CI runs the selected worker argv in the kept `agent/<n>` worktree, re-gates, pushes only on gate PASS, re-reviews, approves only on fresh APPROVE, never requeues: `factory/manage.py:104-131`; covered across gate-fail / REVISE / APPROVE / rebase cases at `tests/test_factory.py:678-736`.
- `ci-fix` and `conflict` profiles in `factory/templates/factory.toml:41-47`, as opt-in commented blocks with host-config placement instructions (`factory/templates/factory.toml:36-39`). Consistent with the issue's "remain host-config, human-applied" clause and README `README.md:100-101`.
- Dashboard exposes `worker_when`: `factory/dashboard.py:847`.
- README updated for the new decision and worker table form: `README.md:88-101`.

## Standards (README conventions)

- Manager remains diagnose-only; the FIX branch is executed by code, mirroring existing dispatch primitives (`worker_round`, `review`, `pr_comment`, `approve_pr`, `escalate`) rather than new mechanisms: `factory/manage.py:117-130`.
- `manage` event recorded before mutation (unchanged ordering at `factory/manage.py:219-221`); failures route to `mechanism_failure` via the existing `except` at `factory/manage.py:222`.

## Non-blocking observations

- `factory/manage.py:66` re-checks `worker in RESERVED_LABELS` although the caller already filters (`factory/manage.py:199`). Harmless; defends direct `parse(…, cfg.workers)` callers.
- `factory/manage.py:124,130`: a failed FIX calls `dispatch.escalate`, which (per README §7) writes a new escalation packet; subsequent manager rounds are bounded by `[manager].rounds`, so no unbounded loop. `[INFERENCE]` from README, not visible in diff.

No findings that fail the acceptance criteria or documented conventions.

VERDICT: APPROVE

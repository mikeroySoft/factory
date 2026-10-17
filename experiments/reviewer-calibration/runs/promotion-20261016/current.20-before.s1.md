## Standards

Repo has no `AGENTS.md`/`CONTRIBUTING.md`; standards source is `docs/manager-plan.md` §4.4 (loop-safety) and T11.

- Decide → record → act: `factory/manage.py:219` records `manage` before `apply` at `:221`; FIX mutations (push/approve/escalate) all happen inside `apply`. Compliant.
- Closed menu applied by code: `factory/manage.py:63-69` validates `worker` against the filtered label set and rejects reserved labels; `:199` filters `RESERVED_LABELS` before both prompt and `parse`. Compliant.
- Human-takeover check still runs before FIX (`factory/manage.py:217`); FIX additionally refuses when a human has `CHANGES_REQUESTED` on the PR (`:108`). Compliant with §4.4 "a human took it".
- `factory doctor` drift: `KNOWN_KEYS["workers"] = None` (`factory/config.py:58`) means `when` is not reported as unknown. No change needed.
- Smell (judgement call, Duplicated Code): `factory/manage.py:116-128` repeats the worker_round → push → review → pr_comment → approve/escalate shape from `dispatch.py:1056-1070`. Not blocking; extraction is not required by any rule and would be a new abstraction beyond the brief.

No hard violations.

## Spec

Issue #20 exit gate vs diff:

- `[workers.<label>]` table-or-array with optional `when`: `factory/config.py:288-298`; legacy array still loads (`tests/test_factory.py:147-165`). ✔
- Manager prompt lists labels with `when`: `factory/manage.py:199-201`. ✔
- ROUTE/FIX only for listed labels: `factory/manage.py:54`, `:63-66`; ROUTE chore without worker → recorded `HUMAN`, with worker → `--add-label chore` (`tests/test_factory.py:652-676`). ✔
- FIX on red CI runs the `ci-fix` argv: `factory/manage.py:116-120` passes `{data["worker"]}` so `cfg.worker` ignores other ticket labels; test asserts the `fix-worker` stub ran (`tests/test_factory.py:678-719`). ✔ Re-gate, push only on PASS, re-review, approve only on fresh APPROVE: `:121-128`. ✔
- `ci-fix` and `conflict` profiles in template: `factory/templates/factory.toml:42-48`, commented, with instructions to copy into host config (`:37-39`). Consistent with "remain host-config, human-applied" and with the template's all-commented convention (uncommenting would also force a `default` in the same table per `config.py:286-287`). ✔
- Out of scope respected: no host-config writes.

## Required fixes

None.

## Optional suggestions

- `factory/manage.py:108-111`: FIX precondition failures raise `ValueError`, which `manage_pass` (`:222-226`) records as `reason = "github_command_failed"` and leaves no issue comment. A `Factory manager:` comment (plan §4.4 attribution rule) or a distinct reason would make the no-op visible to the human. Not a documented requirement for FIX.
- `factory/manage.py:116-128`: on a successful FIX + APPROVE nothing is posted on the issue; only the PR review comment and the `approved` event exist. Consider a `Factory manager:` comment for attribution parity with ROUTE/RETRY (`:154`, `:157`).
- `factory/dashboard.py:847`: `worker_when` is added to the snapshot but `dashboard.html` is not updated to render it. Harmless; either render or drop.
- `factory/templates/factory.toml:44`: a ci-fix worker that declares a flake makes no commit, so the push at `manage.py:124` is a no-op and CI is not re-triggered; the merge stage will see the same red CI and re-escalate (bounded by `manager_rounds`). [INFERENCE] from `dispatch.py:822-844`. Worth a line in README or the profile `when` text if that loop is not intended.

VERDICT: APPROVE

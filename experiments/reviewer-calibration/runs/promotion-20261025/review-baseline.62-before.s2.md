**Evidence note:** `./review.diff` and `./snapshot` do not exist in the sandbox. Reviewed `git diff origin/main...HEAD` (4 commits, `1eca1bb..cfbdb6f`, matching the handoff in the issue comment) against the checked-out gated head; line cites are post-image lines in that checkout. Issue #62 read via GitHub API (gh CLI unavailable).

## Standards

Repo has no AGENTS.md/CONTRIBUTING.md; standards sources are README.md (execution-event contract, config docs) and the `factory init` template. CHANGELOG is release-time only (`289cac5`), so no per-PR entry is owed.

- **Docs assert behavior the code lacks.** `factory/templates/factory.toml:25` (under `[workers]`): "bare `{prompt}` passes path text and is rejected"; `README.md:97-100` says the same in a sentence scoped to "Worker and manager commands". Only `Config.manager_cmd` rejects (`factory/config.py:149-150`); `Config.worker` (`factory/config.py:138-141`) expands anything. Trigger: operator writes `default = ["omp","-p","--cwd","{cwd}","{prompt}"]` expecting the documented rejection → omp silently runs with a path as its prompt. — **Required** (documentation contradicts implemented contract; the issue limits rejection to `manager_cmd` for `omp`).
- Judgement call, Duplicated Code: the omp bare-`{prompt}` rule appears in `factory/config.py:149` and `factory/onboard.py:234`. Optional; a one-function predicate would do, not blocking.
- Judgement call, lifecycle vocabulary: `factory/manage.py:272-273` records `escalate` inside the `manage` lifecycle scope, so `dispatch.record` (`factory/dispatch.py:245-248`, unchanged) stamps the manage execution `outcome=project_escalation, reason=manager_failed`. README:368 defines `project_escalation` as "not a broken runtime mechanism", while README:371-372 route a mechanism that could not run to `mechanism_failure`/`unknown`. Runtime state still projects to `completed` either way (`factory/lifecycle.py:642`), so no consumer-visible state flips. Optional.

## Spec (issue #62 + agent brief)

Satisfied:
- `manager_cmd(prompt_path, cwd)` expands a path; `manage.py:263-265` writes `<factory>/manager-prompt-<n>.md`. `learn.py:64-68` adapted (temp file, same prefix). ✓
- omp bare `{prompt}` → `ConfigError` naming `@{prompt}`; other programs untouched (`config.py:149-151`). ✓ Test `tests/test_factory.py:150-165`.
- Nonzero exit: `Manager command exited N (argv: <unexpanded json>)` + last 5 stderr lines + `escalate` event `reason=manager_failed` (`manage.py:266-273`). ✓ Test `tests/test_factory.py:752-782`. Round arithmetic excludes `manager_failed` (`dispatch.py:375-378`), approved in review round 1 per the issue comment.
- `ConfigError` contained per ticket (`manage.py:278`), approved in round 1. ✓
- Doctor row WARN/FAIL/PASS (`onboard.py:226-238`). ✓ Test `tests/test_factory.py:410-436`.
- README §Configuration + template document file-vs-text (`README.md:97-100`, template `:25,:63-65`). ✓
- `DEFAULT_MANAGER`: does not exist on base or head (manager unset = disabled); the README/template example commands now use `@{prompt}`. Not applicable rather than missing.

Defects:
- **Unrequested behavior loss in the `ci-fix`/`conflict` profiles.** `factory/templates/factory.toml:44` and `:48` replace `"Read the ticket prompt at {prompt}. Read the failing CI job log. Fix the cause, or declare a flake … Do not claim a flake without evidence."` (and the conflict equivalent) with a bare `@{prompt}`. Those strings were already valid omp inline prompts with a *path* substituted — not the bug #62 fixes — and they were the profile's only instruction channel: `worker_when` is shown to the manager only (`factory/manage.py:252-253`), never to the worker. Trigger: a human applies the template profile as README:119-120 directs → ROUTE adds the label → the worker runs with no profile-specific instructions. Issue scope is "`factory init` template … document `{prompt}` = file"; nothing in "After this change" or the brief asks to change profile commands. — **Required**: revert the two command lines (they need no `@`).
- Stats: the issue says the event exists "so `factory stats` can count it", but `stats.py:167-171` (unchanged) folds it into `escalation_count`/`escalations_per_week`, inflating per-ticket escalation counts by one per manager failure. No stats change was in the brief; flagging as **Optional** (filter `reason == "manager_failed"` or add a distinct counter).

## Plan drift

Plan not recorded; drift judged against the issue's "Touches".
- Named: `config.py`, `manage.py`, `doctor.py` (→ `onboard.py` here; `doctor.py` does not exist), template, README, `tests/test_factory.py`.
- Not named: `factory/dispatch.py` (round exclusion, approved in round 1), `factory/learn.py` (forced by the `manager_cmd` signature change). Neither exposes a defect.
- Proof missing from the gate report: the manual exit-gate item "re-run `factory manage` on District #26's kept packet … and get a real DECISION line". The handoff claims it from a prior attempt (`.factory/district-26-replay.log`) and marks it unverified for this revision; the gate report (conflict-markers/test/leak-scan) carries no evidence of it. Not a code defect; noting for the operator.

## Required fixes
1. `factory/templates/factory.toml:44,48` — restore the original `ci-fix`/`conflict` command strings (unrequested removal of worker instructions; `worker_when` never reaches the worker).
2. `factory/templates/factory.toml:25`, `README.md:100` — scope "is rejected" to the manager command (only `Config.manager_cmd` validates; `Config.worker` does not).

## Optional
- Dedupe the omp bare-`{prompt}` predicate (`config.py:149`, `onboard.py:234`).
- Decide whether a manager nonzero exit should stamp the `manage` execution `project_escalation` (README:368) vs `mechanism_failure`/`unknown` (README:371-372).
- Exclude `reason == "manager_failed"` from `stats.py:167` or count it separately.

VERDICT: REVISE

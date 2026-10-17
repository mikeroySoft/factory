## Standards

Sources: no `AGENTS.md`/`CONTRIBUTING.md` in repo; the documented contract is `README.md` (notably the execution event contract, README:364-372) and `factory/templates/factory.toml`. Smell baseline applied as judgement calls.

### Required

**S1. Manager failure is classified as `project_escalation` in the lifecycle journal — contradicts README event contract.**
`factory/manage.py:272-273` calls `dispatch.record("escalate", …, reason="manager_failed")` inside the `manage` lifecycle scope (`manage.py:225`). `dispatch.record` (dispatch.py:245-248, pre-existing) sets `execution.outcome = "project_escalation"` and `execution.reason = "manager_failed"` for every `escalate` event. README:368 defines `project_escalation` as "the existing project escalation path ran; **not a broken runtime mechanism**"; README:371-372 route "missing/permission-denied executable" to `mechanism_failure` and "unexplained worker/reviewer nonzero exits" to `unknown`. Trigger: manager exits nonzero. Impact: the `manage` execution exits `outcome=project_escalation`, which maps to `state: completed` (README:394-395, lifecycle.py:642), so runtime views show a failed manager as a normally completed escalation. Minimal fix: after the `record` call set `execution.outcome = "unknown"` (or `"mechanism_failure"`) and `execution.reason = "manager_failed"`, or append the row without going through `record`'s outcome mapping.

### Optional

- **Duplicated Code (judgement call):** the omp bare-`{prompt}` rule lives in both `factory/config.py:149-150` and `factory/onboard.py:233-234`. Doctor could call `cfg.manager_cmd(Path("x"), Path("."))` in a `try/except ConfigError` and reuse the message. Not blocking.
- `factory/stats.py:167-171` counts every `escalate` event into `escalation_count`/`escalation_times`; after this diff one real escalation + one manager failure reports `escalation_count = 2`. The issue text asked for an `escalate`-shaped event "so `factory stats` can count it", so this may be intended; if not, mirror the `reason != "manager_failed"` exclusion already added at `factory/dispatch.py:376`.

## Spec

Issue #62 + agent brief. Verified against the diff:

- `manager_cmd(prompt_path, cwd)` expands `{prompt}` with the path; omp + bare `{prompt}` raises `ConfigError` naming `@{prompt}`; other programs untouched — `factory/config.py:146-151`. ✔
- Prompt written to `<factory>/manager-prompt-<n>.md` — `factory/manage.py:263-264`. ✔ (`learn.py:64-68` uses a temp file with the same prefix; acceptable, no ticket number there.)
- Nonzero exit posts `Manager command exited N (argv: <unexpanded>)` + last 5 stderr lines; `escalate` event with `reason: manager_failed` — `manage.py:266-273`. ✔ Round arithmetic excludes it — `dispatch.py:375-378`. ✔
- Doctor `manager command` row WARN/FAIL/PASS — `onboard.py:226-238`. ✔
- Tests for `@<path>` readable content, ConfigError, HUMAN body/5-line cap, doctor rows — `tests/test_factory.py:150-165, 410-436, 730-797`. ✔
- `DEFAULT_MANAGER` named in "Touches" does not exist in `config.py` (manager default is `None`, config.py:105); the template example is the effective default and was updated — `factory/templates/factory.toml:65`. Acceptable.

### Required

**P1. README/template state that a bare worker `{prompt}` for omp "is rejected" — false.**
`factory/templates/factory.toml:25` (under `[workers]`): "OMP file prompts require `@{prompt}`; bare `{prompt}` passes path text and is rejected." `README.md:97-100`: "Worker and manager commands receive `{prompt}` as a prompt-file path… bare `omp {prompt}` passes the path as prompt text and is rejected." Only `Config.manager_cmd` rejects (`config.py:149`); `Config.worker` (`config.py:138-141`) performs no check and doctor checks only the manager (`onboard.py:226-238`). The issue explicitly scopes rejection to the manager ("`manager_cmd` rejects a bare `{prompt}` element… Other programs are not second-guessed") and the acceptance criterion requires README §Configuration and the template to *document* the contract. Documenting a rejection that does not happen misleads an operator into expecting a config error instead of a silently broken worker. Fix: scope the sentence to the manager (or drop "and is rejected" in the worker context).

**P2. Unrequested rewrite of the `ci-fix` / `conflict` worker profiles removes their defining instructions.**
`factory/templates/factory.toml:44` and `:48` replace `["omp", "-p", "--cwd", "{cwd}", "Read the ticket prompt at {prompt}. Read the failing CI job log. Fix the cause, or declare a flake…"]` with `["omp", "-p", "--cwd", "{cwd}", "@{prompt}"]`. The old commands were valid omp (inline text embedding the path, not a bare `{prompt}` element) and were not in scope — the issue's "Touches" lists the init template only for the `{prompt}` contract note, and worker profiles are not mentioned. `worker_when` is only fed to the manager prompt (`manage.py:253`), never to the worker, so after this change an operator who uncomments `ci-fix` gets a worker identical to `default` with no CI-log/flake-evidence instruction — the behavior CHANGELOG:29 and docs/manager-plan.md:344-346 define these profiles by. Fix: revert those two lines.

### Optional

- `manage.py:278-279`: the `ConfigError` path posts `Manager command failed: …` but records no `manager_failed` event and leaves the lifecycle outcome `completed`, unlike the nonzero-exit path. Brief only specifies the nonzero-exit case; noting the asymmetry.

---
Standards: 1 required (lifecycle outcome misclassified), 2 optional. Spec: 2 required (false rejection claim in docs; unrequested profile regression), 1 optional.

VERDICT: REVISE

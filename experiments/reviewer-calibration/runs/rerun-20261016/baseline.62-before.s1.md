## Findings

### Required

**R1 — Template and README claim a worker-side rejection the diff does not implement.**
`factory/templates/factory.toml:25` (under `[workers]`): "bare `{prompt}` passes path text and is rejected." `README.md:99-100`: "Worker and manager commands receive `{prompt}` as a prompt-file path … bare `omp {prompt}` … is rejected." The only rejection in the diff is `factory/config.py:149-150`, inside `Config.manager_cmd`; `Config.worker` is untouched and the issue explicitly scopes the check to the manager ("Other programs are not second-guessed"). The acceptance criterion is to *document* the `{prompt}` contract; documenting a check that doesn't exist for workers fails that. Fix: scope the rejection sentence to the manager (or move the template line to the `[manager]` block, where `factory.toml:64` already says it correctly). `[INFERENCE]`: no pre-existing worker validation is visible in the diff; if one exists at `config.py:135`, downgrade to optional.

**R2 — Unrequested behavior change to the shipped `ci-fix` / `conflict` profiles.**
`factory/templates/factory.toml:44` and `:48` replace `"Read the ticket prompt at {prompt}. Read the failing CI job log. Fix the cause, or declare a flake…"` / `"…Resolve the rebase conflicts, preserving both sides' intents…"` with `"@{prompt}"`. The old commands were valid under the worker contract (`{prompt}` is a path embedded in inline text) and carried the profile's task instructions; the new ones drop that guidance, leaving only the `when` routing rule. Issue #62 touches the template only to document `{prompt}` = file; nothing asks to change these examples. Revert both lines.

### Optional / risk (not blocking)

- `factory/manage.py:271-272` records an `escalate` event (`reason: manager_failed`) *before* `apply` records the `manage` decision. `tests/test_factory.py` `test_manager_failure_bounds_stderr_and_records_reason` patches `manage.apply`, so the manage event is never written in that test; whether the extra `escalate` row makes the same ticket eligible again on the next `manage` pass (without a new real escalation) is unverified. The ConfigError test (`test_manager_config_error_leaves_diagnosis_without_replaying`) covers no-replay only for the path that records no `escalate` event (`manage.py:278`). Suggest extending the nonzero-exit test to a second `manage_pass()` without a new escalation and asserting no second `apply`.
- Issue text names `DEFAULT_MANAGER` in `config.py`; the diff changes no such constant. `config.py:148` (`self.manager or []`) and README "Disabled unless `manager.command` is configured" indicate there is no default manager command, so this is likely an issue-text inaccuracy, not a gap. `[INFERENCE]`.
- `factory/manage.py:263-264` leaves `manager-prompt-<n>.md` on disk after the run; matches the worker transport, fine.

### Verified against acceptance criteria

- `manager_cmd(prompt_path, cwd)` path transport + omp bare-`{prompt}` `ConfigError` naming `@{prompt}`: `factory/config.py:146-151`; test `tests/test_factory.py:150-165`.
- Nonzero exit → `Manager command exited N (argv: <unexpanded>)` + ≤5 stderr lines + `manager_failed` event: `factory/manage.py:266-272`; test asserts exactly lines 15–19 of 20.
- Round arithmetic excludes `manager_failed`: `factory/dispatch.py:376-377`.
- Doctor row WARN/FAIL/PASS with placeholder/executable/omp-shape detail: `factory/onboard.py:226-238`; 8-case table in `test_doctor_manager_command`.
- `learn.py` manager adapter moved to file transport: `factory/learn.py:64-68`.
- README §Configuration gains `[manager]` example with `@{prompt}`: `README.md:215-216`.

VERDICT: REVISE

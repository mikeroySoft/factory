# Review — issue #62, head `cfbdb6f`

## Spec coverage

| Criterion | Evidence | Status |
|---|---|---|
| `manager_cmd(prompt_path, cwd)` expands `{prompt}` with a file path; manager prompt lives at `<factory>/manager-prompt-<n>.md` | `factory/config.py:146-151`, `factory/manage.py:263-265`; test `tests/test_factory.py:150-165`, `:730-750` | Met (file is written by the caller, not inside `manager_cmd`; the exit-gate assertion — `@<path>` readable with prompt content — holds) |
| Bare `{prompt}` for `omp` → `ConfigError` naming `@{prompt}` | `factory/config.py:149-150`; test `:163-165` | Met |
| Nonzero exit → `Manager command exited N (argv: …unexpanded…)` + ≤5 stderr lines, `escalate` event `reason: manager_failed` | `factory/manage.py:266-272`; test `:752-782` | Met |
| `factory doctor` `manager command` row: WARN unset / FAIL missing placeholder or executable / PASS | `factory/onboard.py:226-238`; test `:410-436` | Met |
| README §Configuration + init template document file-vs-text contract | `README.md:97-101`, `README.md:215-216`, `factory/templates/factory.toml:25,50,63-65` | Met with one inaccuracy (below) |
| `learn.py` caller migrated to the path contract | `factory/learn.py:64-68` | Met |
| `ConfigError` contained per ticket; `manager_failed` events excluded from round arithmetic | `factory/manage.py:278`, `factory/dispatch.py:376-377`; tests `:784-796`, `:773-782` | Met |

Not verifiable from the diff: the issue's "Touches" names `DEFAULT_MANAGER` in `config.py`; the diff does not change it. The pre-existing doctor test (`tests/test_factory.py` removed lines 393-401) shows the manager is unset by default, so there is likely no active inline-text default that the new `omp` rejection would break. `[INFERENCE]` — confirm `config.py` has no `DEFAULT_MANAGER = [... "omp", ..., "{prompt}"]` still in use.

## Required fixes

1. **Documentation asserts a validation that does not exist for workers.**
   - `README.md:100`: "bare `omp {prompt}` passes the path as prompt text and is rejected" — stated under "Worker and manager commands".
   - `factory/templates/factory.toml:25` (under `[workers]`): "bare `{prompt}` passes path text and is rejected."
   - The only rejection in the diff is `Config.manager_cmd` (`factory/config.py:149-150`); `Config.worker` and the doctor worker rows (`factory/onboard.py:223-224`) are unchanged. An operator with `default = ["omp", "-p", "--cwd", "{cwd}", "{prompt}"]` gets no error, no doctor FAIL, and a worker that receives a filename as its prompt — the README tells them otherwise.
   - Rule: acceptance criterion "README.md §Configuration and the `factory init` template document `{prompt}` = file for workers **and** manager" — the documentation must describe actual behavior. Fix: scope the "is rejected" claim to the manager command (as `factory/templates/factory.toml:64` already correctly does), or drop it from the worker sections. Do not add worker validation; the issue explicitly limits rejection to `manager_cmd`.

## Optional suggestions

- `factory/templates/factory.toml:44,48`: the `ci-fix` and `conflict` profile commands lost their task-specific inline instructions ("Read the failing CI job log… declare a flake with evidence", "Resolve the rebase conflicts… no semantic changes"). Those commands were not bare `{prompt}` elements, so nothing in this change rejects them, and the issue only asks to document the placeholder contract. Recommend restoring the original commands; the behavioral content of those example profiles is outside the brief.
- `factory/manage.py:268`: when the manager writes its error to stdout only (stderr empty), the HUMAN body carries no diagnostic beyond the argv line. The issue specifies a stderr tail, so this is conformant; consider falling back to the last 5 stdout lines when stderr is empty.
- `factory/manage.py:272`: the `manager_failed` escalate event is appended before the `manage` HUMAN decision is recorded. `test_manager_failure_bounds_stderr_and_records_reason` patches `manage.apply`, so it does not exercise whether a subsequent `manage_pass` without a new real escalation treats that event as an untouched escalation and re-runs the manager. `test_manager_config_error_leaves_diagnosis_without_replaying` covers only the `ConfigError` path, which records no such event. `[INFERENCE]` likely fine since the `manage` event lands after it in the same pass, but a non-replay assertion on the nonzero-exit path would close the gap.
- `factory/config.py:146-151` vs. issue text: the issue says `manager_cmd` writes the file; here callers do (`manage.py:263-264`, `learn.py:65-67`). Behaviorally equivalent and simpler; no action needed unless the maintainer wants the signature to match the issue literally.

VERDICT: REVISE

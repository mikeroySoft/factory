# Review: issue #62 — manager prompt transport (head `cfbdb6f`)

## Spec coverage

| Acceptance criterion | Evidence | Status |
|---|---|---|
| `manager_cmd(prompt_path, cwd)` expands `{prompt}` with a file path; prompt written to `<factory>/manager-prompt-<n>.md` | `factory/config.py:146-151`; `factory/manage.py:263-265` | Met (write happens in caller, signature as specified) |
| Bare `{prompt}` for argv[0] `omp` → `ConfigError` naming `@{prompt}`; other programs not second-guessed | `factory/config.py:149-150` (`Path(argv[0]).name == "omp"`, exact-element match so `@{prompt}` passes) | Met |
| Nonzero exit → `Manager command exited N (argv: …unexpanded…)` + last 5 stderr lines; `escalate` event with `reason: manager_failed` | `factory/manage.py:267-273` | Met |
| `factory doctor` `manager command` row: WARN unset / FAIL missing placeholder or executable / PASS | `factory/onboard.py:226-238` | Met |
| README §Configuration + init template document file for workers+manager, text for reviewer | `README.md:37-39,97-100,215-216`; `factory/templates/factory.toml:25,50,63-65` | Met |
| Exit-gate tests (`@<path>` readable content; ConfigError; HUMAN body ≤5 lines; doctor WARN/FAIL) | `tests/test_factory.py:150-165,410-436,730-780` | Met |
| Default manager command becomes worker shape (`DEFAULT_MANAGER`) | Template default updated (`factory.toml:65`); no `DEFAULT_MANAGER` change in diff | `[INFERENCE]` No code default exists (README: "unset command disables it"). Not verifiable from the diff. |

Approved scope change (round-1 fixes): `ConfigError` caught at execution boundary (`factory/manage.py:278`) and `manager_failed` events excluded from round arithmetic (`factory/dispatch.py:376-377`), with regressions at `tests/test_factory.py:757-780,782-794`. Consistent with the handoff notes.

## Required fixes

1. **Documentation asserts worker-command validation that does not exist.**
   `README.md:97-98`: "Worker and manager commands receive `{prompt}` as a prompt-file path … bare `omp {prompt}` passes the path as prompt text **and is rejected**." `factory/templates/factory.toml:25` (under `[workers]`): "bare `{prompt}` passes path text **and is rejected**."
   The only rejection in the diff is `Config.manager_cmd` (`factory/config.py:149-150`); `Config.worker` is untouched and `doctor` checks only `shutil.which` for workers (`factory/onboard.py:223-224`, unchanged). Trigger: operator writes `default = ["omp", "-p", "--cwd", "{cwd}", "{prompt}"]` trusting the docs; impact: no `ConfigError`, no doctor FAIL, worker silently receives the path string as its prompt on first dispatch. Correctness defect in text introduced by this diff. Fix: scope the "is rejected" clause to the manager (or validate workers too — but that exceeds the issue's "other programs are not second-guessed"/manager-only brief; the doc qualifier is the simpler change).

## Optional suggestions

- `factory/templates/factory.toml:44,48`: the commented `ci-fix`/`conflict` profile commands were rewritten from task-specific inline instructions (restart aborted rebase, no-flake-without-evidence) to plain `@{prompt}`. Not requested by #62 and drops guidance text; the original form (`Read the ticket prompt at {prompt}. …`) was not a bare `{prompt}` element and worked. Consider reverting to keep the diff scoped.
- `factory/manage.py:271-272` records an `escalate` event per failure; `factory stats` counts trace `escalate` events (README "Trace escalation counts likewise supplement timeline counts"), so manager failures will inflate the escalation count unless stats filters `reason == "manager_failed"`. Issue asked for this event shape, so not a defect against spec—flagging the side effect.
- `tests/test_factory.py:757-780` proves round arithmetic after a `manager_failed` event but does not run `manage_pass` a second time *without* a fresh escalation to prove the failed round isn't replayed (the ConfigError test at `:782-794` does). One extra `manage_pass()` + "no new comment" assertion would close that gap. `[INFERENCE]` likely fine since the `manage` event is recorded after the `escalate` event.
- `factory/onboard.py:233` duplicates the omp bare-`{prompt}` predicate from `factory/config.py:149`. Could call `cfg.manager_cmd(Path("x"), Path("."))` under `try/except ConfigError` to keep one source of truth. Minor.

VERDICT: REVISE

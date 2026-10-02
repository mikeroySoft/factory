## Spec (issue #62)

| Criterion | Evidence | Status |
|---|---|---|
| `manager_cmd(prompt_path, cwd)` expands `{prompt}` with a file path | `factory/config.py:146-151`; file written at `factory/manage.py:263-265` | Met. Write happens in the caller rather than inside `manager_cmd` as the issue's wording suggests; behavior and exit-gate test (`tests/test_factory.py:150-165`, `:730-749`) are equivalent. Non-blocking. |
| Bare `{prompt}` for `omp` → `ConfigError` naming `@{prompt}`; other programs not second-guessed | `factory/config.py:149-150`; `Path(argv[0]).name` handles absolute paths; test `:163-165` | Met. |
| Default manager command becomes worker shape | `factory/templates/factory.toml:63-65`, `README.md:215-216` | Met for template/README. `DEFAULT_MANAGER` in `config.py` not touched by the diff; `cfg.manager or []` (`config.py:148`) and README "unset command disables it" imply no code default exists. `[INFERENCE]` — cannot confirm from the packet. |
| Nonzero exit → `Manager command exited N (argv: …unexpanded…)` + last 5 stderr lines; `escalate` event with `reason: manager_failed` | `factory/manage.py:267-273`; test `:751-779` asserts prefix, unexpanded `{prompt}`/`{cwd}`, exactly lines 15–19, and the event tuple | Met. |
| `doctor` `manager command` row: WARN unset / FAIL missing placeholder or executable / PASS | `factory/onboard.py:226-238`; test `:410-436` covers all branches plus text output | Met. Extra omp bare-`{prompt}` check (`onboard.py:234-235`) is consistent with the `ConfigError` rule. |
| `ConfigError` at runtime does not abort the pass | `factory/manage.py:278` catches `config.ConfigError`; test `:781-794` | Met (round-1 reviewer fix). |
| `manager_failed` events excluded from round arithmetic | `factory/dispatch.py:376-377`; test `:770-779` proves next escalation is round 2 | Not in issue text, but necessary so a config failure doesn't consume the operator's round budget. Justified. |
| `learn` adapter uses the file transport | `factory/learn.py:64-68` | Met. |

## Standards (README conventions)

**Required fix — documentation claims rejection that the code does not perform.**

- `README.md:99-100`: "bare `omp {prompt}` passes the path as prompt text and is rejected", stated in a sentence covering **worker and manager** commands.
- `factory/templates/factory.toml:25` (under `[workers]`): "bare `{prompt}` passes path text and is rejected."

The rejection exists only in `Config.manager_cmd` (`factory/config.py:149-150`). `Config.worker` is unchanged by this diff, and the doctor check at `factory/onboard.py:234-235` only inspects `cfg.manager`. An operator with `default = ["omp", "-p", "--cwd", "{cwd}", "{prompt}"]` is told it will be rejected; it will instead run `omp -p <path>` silently — the exact silent-misconfiguration class this issue addresses. Fix is wording: scope both sentences to the manager command (e.g. "…and the manager command rejects it"), or state that worker commands are not validated. Extending validation to workers would be scope creep beyond the issue ("Other programs are not second-guessed" applies to the manager only).

## Non-blocking notes

- `factory/manage.py:268` builds the tail from `proc.stderr` only; the previous `proc.stderr or proc.stdout` fallback is dropped. Matches the issue text ("last 5 stderr lines"); a manager that reports failure on stdout now yields a one-line body. Acceptable per spec.
- `factory/manage.py:263` leaves `manager-prompt-<n>.md` in `.factory/`; consistent with the worker's `.factory-prompt.md` and the issue's stated path.

VERDICT: REVISE

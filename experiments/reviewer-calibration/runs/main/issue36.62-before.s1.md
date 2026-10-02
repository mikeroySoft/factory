## Review: issue #62, `f6dfc77..cfbdb6f`

### Spec coverage

| Criterion | Evidence | Status |
|---|---|---|
| `manager_cmd(prompt_path, cwd)` expands `{prompt}` with a file path; prompt written to `<factory>/manager-prompt-<n>.md` | `factory/config.py:146-151`, `factory/manage.py:263-265` | Met (file written by caller, consumed by `manager_cmd`; `tests/test_factory.py:150-165` asserts `@<path>` + readable content) |
| Bare `{prompt}` for `omp` → `ConfigError` naming `@{prompt}` | `factory/config.py:149-150`; exact-element match so `@{prompt}` passes | Met; contained at runtime via `factory/manage.py:278` |
| Nonzero exit → `Manager command exited N (argv: …unexpanded…)` + ≤5 stderr lines, `escalate`/`manager_failed` event | `factory/manage.py:267-273`; `tests/test_factory.py:754-785` | Met. `dispatch.py:376-377` excludes the synthetic event from round arithmetic so the round budget isn't consumed. |
| `doctor` row: WARN unset / FAIL missing placeholder or executable / PASS | `factory/onboard.py:226-238`; `tests/test_factory.py:410-436` | Met |
| README §Configuration + `init` template document file-vs-text contract | `README.md:35-38,97-105,215-216`; `factory/templates/factory.toml:25,63-65` | Met, with the defect below |
| Default manager command is worker shape | `README.md:216`, `factory.toml:65` | Met for template/README. `DEFAULT_MANAGER` in `config.py` not in the diff; `[INFERENCE]` no such constant exists since "unset command disables" (`factory.toml:62`). |

### Required fixes

1. **Docs claim a validation that does not exist for workers.**
   `README.md:99-100`: "Worker and manager commands receive `{prompt}` as a prompt-file path… bare `omp {prompt}` passes the path as prompt text and is rejected." `factory/templates/factory.toml:25` (under `[workers]`): "bare `{prompt}` passes path text and is rejected."
   The only rejection in the diff is `factory/config.py:149-150`, reached solely from `manager_cmd`; `Config.worker` is untouched and `onboard.py:223-224` still checks workers only for PATH resolution. Trigger: `[workers].default = ["omp","-p","--cwd","{cwd}","{prompt}"]`. Impact: no error from `config` or `doctor`; omp receives the path as prompt text; every claimed ticket fails with the operator having been told the config would be rejected. Rule: the issue scopes rejection to the manager ("Other programs are not second-guessed"; worker contract unchanged), and the README is this repo's documented contract — the diff makes it false. Fix: scope the sentence in `README.md:99-100` and the comment at `factory.toml:25` to the manager command (or drop the worker-side "is rejected" claim). Extending validation to workers is out of scope and not required.

### Optional suggestions

- `factory/templates/factory.toml:44,48`: the `ci-fix`/`conflict` profile examples replaced task-specific inline instructions ("Fix the cause, or declare a flake… Do not claim a flake without evidence") with a bare `@{prompt}`. The old form was a valid, working shape (path embedded in instruction text); the issue asked only to *document* the contract. The profiles now differ from `default` only by `when`; consider restoring the guidance (e.g. as a second `@{prompt}`-compatible instruction argument) or noting it was intentionally dropped.
- `factory/learn.py:64-68`: `manager_cmd` can now raise `ConfigError` here too; `manage.py:278` handles it but `learn` will surface it however the CLI treats `ConfigError` globally. Acceptable if the top-level handler formats it; otherwise a one-line cause would match `manage`'s behavior.
- `factory/manage.py:264-265`: on the `ConfigError` path the prompt file is written before the raise and left behind. Harmless; mention only.
- `factory/manage.py:271-272`: the `manager_failed` row shares `event: "escalate"` with real escalations. `dispatch.py:376-377` compensates for round counting; `stats`/dashboard escalation counts will include it (as the issue requests). Any other consumer of `escalate` events not shown in this diff should be checked for the same assumption — `[INFERENCE]`, outside the packet.

VERDICT: REVISE

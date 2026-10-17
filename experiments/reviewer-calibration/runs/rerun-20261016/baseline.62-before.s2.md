## Spec (issue #62)

| Acceptance criterion | Evidence | Status |
|---|---|---|
| `manager_cmd(prompt_path, cwd)` expands `{prompt}` with a file path; manager writes `<factory>/manager-prompt-<n>.md` | `factory/config.py:146-151`; `factory/manage.py:263-265` | Met |
| Bare `{prompt}` for argv[0] `omp` → `ConfigError` naming `@{prompt}`; other programs not second-guessed | `factory/config.py:149-150` (`Path(argv[0]).name == "omp"` gate, exact-element match so `@{prompt}` passes) | Met |
| Default manager command in worker shape | `factory/templates/factory.toml:63-65`; `README.md:215-216` | Met. No `DEFAULT_MANAGER` constant appears in the diff; pre-existing `self.manager or []` (`config.py:146`) indicates manager is unset-by-default, so the template comment is the only "default" `[INFERENCE]` |
| README §Configuration + `init` template: `{prompt}` = file for workers and manager, text for reviewer | `README.md:97-104`, `README.md:212-216`; `factory/templates/factory.toml:25`, `:50`, `:63-64` | Met |
| `doctor` `manager command` row: WARN unset / FAIL missing placeholder or executable / PASS | `factory/onboard.py:226-238` | Met. Also flags bare omp `{prompt}` (`onboard.py:234-235`), consistent with the `ConfigError` path |
| Nonzero exit posts `Manager command exited N (argv: …unexpanded…)` + last 5 stderr lines; records `escalate` event `reason: manager_failed` | `factory/manage.py:266-273` (`json.dumps(cfg.manager)` is the unexpanded template; `splitlines()[-5:]`) | Met |
| `ConfigError` contained per ticket instead of aborting the pass | `factory/manage.py:278` | Met (reviewer round-1 fix) |
| `manager_failed` events excluded from round arithmetic | `factory/dispatch.py:376-377` | Consistent: the event is recorded for stats but does not consume a manager round |
| Exit-gate tests: `@<path>` readable content; bare omp `{prompt}` raises; HUMAN body prefix + ≤5 lines; doctor WARN/FAIL rows | `tests/test_factory.py:150-165`, `:410-436`, `:730-749`, `:751-784`, `:786-797` | Met. `:775` asserts exactly `error-15..19` (last 5 of 20) |
| `learn.py` caller migrated to path transport | `factory/learn.py:64-68` | Met; `NamedTemporaryFile` in `cfg.factory` with `manager-prompt-` prefix, removed after the call |

Out-of-scope items (decision semantics, packet format, model) untouched. Legacy test stubs that read `sys.argv[2]` as text updated to read the file (`tests/test_factory.py:861`, `:983`, `:1219`) — required by the contract change, not scope creep.

## Standards

No AGENTS.md/CONTRIBUTING.md at this head. README review rules require `path:line` citations and acceptance-criterion grounding; nothing in the diff conflicts with documented conventions. The `[manager]` section was added to the README config example that previously omitted it (`README.md:215-216`), matching the template.

## Non-blocking observations

- `factory/manage.py:263-264`: prompt file is written before `manager_cmd` validates; on `ConfigError` the file persists in `.factory/`. Gitignored; harmless.
- Manual District #26 replay cannot be evidenced from the diff; handoff reports it was done in a prior attempt.

VERDICT: APPROVE

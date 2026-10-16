# Review: #62 — manager prompt transport, failure body, doctor row

## Spec coverage (issue "After this change" + exit gate)

| Criterion | Evidence | Status |
|---|---|---|
| `manager_cmd(prompt_path, cwd)` expands `{prompt}` with a file path | `factory/config.py:146-151`; file written at `factory/manage.py:264-265` (`<factory>/manager-prompt-<n>.md`) and `factory/learn.py:64-68` | Met (writing is done by callers, not inside `manager_cmd`; see Optional 1) |
| Reject bare `{prompt}` for omp with `ConfigError` naming `@{prompt}` | `factory/config.py:148-149`; exact-element match, basename check, other programs untouched | Met |
| Default/template manager command becomes worker shape | `factory/templates/factory.toml:63-65`, `README.md:219-220` | Met for template/README. The issue names a `DEFAULT_MANAGER` constant in `config.py`; the diff touches none, and README states the manager is disabled when `command` is unset, so no runtime default appears to exist. `[INFERENCE]` — not citable from the diff. |
| README §Configuration + init template document file-vs-text | `README.md:97-102`, `factory/templates/factory.toml:25`, `:51`, `:63-64` | Met |
| Doctor row: WARN unset / FAIL missing placeholder or exe / PASS | `factory/onboard.py:226-238`; test matrix `tests/test_factory.py:410-436` | Met |
| Nonzero exit → `Manager command exited N (argv: …unexpanded…)` + ≤5 stderr lines | `factory/manage.py:267-270` (`json.dumps(cfg.manager)` is the unexpanded argv; `splitlines()[-5:]`) | Met |
| Records `escalate` event with `reason: manager_failed` | `factory/manage.py:272-273`; consumers filter it at `factory/dispatch.py:376-377`, `factory/manage.py:237-238`, `factory/stats.py:167-168` so a failure neither consumes a round nor inflates escalation counts | Met |
| Exit-gate tests | `tests/test_factory.py:150-165` (`@<path>`, readable content, ConfigError regex), `:752-790` (body prefix, exactly 5 trailing stderr lines, event, round 2 on next escalation), `:410-436` (doctor WARN/FAIL) | Met |
| Round-1 reviewer fixes (ConfigError containment, round arithmetic) | `factory/manage.py:279`, `factory/dispatch.py:376-377`; tests `:792-804`, `:835-858` | Met |

Out-of-scope items (decision semantics, packet format, model) untouched.

## Standards

No AGENTS.md/CONTRIBUTING.md at this head. README conventions satisfied: CHANGELOG entry (`CHANGELOG.md:4-6`), `.factory/` prompt files stay in the gitignored state dir, `manage` event recorded before GitHub mutation (unchanged path), stats JSON additive (`factory/stats.py:171-172`) with README documenting the new field (`README.md:144-146`).

## Required fixes

None.

## Optional suggestions

1. `factory/manage.py:264-265` and `factory/learn.py:64-68` each write the prompt file independently. The issue's wording placed the write inside `manager_cmd`; the split keeps `Config` free of I/O, which is reasonable, but the two call sites now own the file-naming contract separately (`manager-prompt-<n>.md` vs `NamedTemporaryFile(prefix="manager-prompt-")`). Non-blocking.
2. `factory/manage.py:268` relies on `proc.stderr` being a `str`. The pre-change code (`proc.stderr or proc.stdout`) implies text capture, so this holds; if `dispatch.run` ever returns `None` stderr this line raises `AttributeError` outside the `(OSError, ConfigError)` catch. Not citable as a defect from the diff.
3. `factory/learn.py:66` uncaught `ConfigError` from `manager_cmd` surfaces as a CLI error for a bare-`{prompt}` omp config in `factory learn`. Consistent with other config errors; noting only that it is a new failure mode for that command.

VERDICT: APPROVE

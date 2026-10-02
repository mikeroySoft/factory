## Spec check (issue #62)

| Criterion | Evidence | Status |
|---|---|---|
| `manager_cmd(prompt_path, cwd)` expands `{prompt}` with a file path | `factory/config.py:146-151`; prompt file written at `factory/manage.py:264-266` as `<factory>/manager-prompt-<n>.md` | Met. File write lives in the caller rather than inside `manager_cmd` as the issue prose says; signature takes a path, so the split is the only coherent reading of the brief. Exit-gate test `tests/test_factory.py:150-165` asserts `@<path>` and readable content. |
| Bare `{prompt}` for `omp` → `ConfigError` naming `@{prompt}` | `factory/config.py:149-150`; test `tests/test_factory.py:164-165`. `Path(argv[0]).name` also covers absolute `omp` paths. Other programs untouched. | Met. |
| `factory doctor` `manager command` row: WARN unset, FAIL missing placeholder/executable, PASS otherwise | `factory/onboard.py:226-238`; tests `tests/test_factory.py:410-436` cover all three statuses plus the omp bare-`{prompt}` FAIL | Met. |
| Nonzero exit posts `Manager command exited N (argv: …unexpanded…)` + ≤5 stderr lines, records `escalate` with `reason: manager_failed` | `factory/manage.py:268-275`; test `tests/test_factory.py:759-769` asserts prefix, unexpanded `{prompt}`/`{cwd}`, exactly lines 15–19 | Met. |
| README §Configuration + init template document file-vs-text contract; default manager command is worker shape | `README.md:97-106`, `README.md:219-221`, `factory/templates/factory.toml:25`, `factory/templates/factory.toml:63-65` | Met for docs/template. `DEFAULT_MANAGER` in `config.py` is named in "Touches" but does not appear in the diff; `[INFERENCE]` manager is optional (`self.manager or []`, `config.py:148`) so there may be no runtime default to change. Not citable from the diff; not a blocking finding. |
| `factory learn` uses the same transport | `factory/learn.py:64-68` (temp file under `cfg.factory`, `manager-prompt-*` prefix) | Met; not required by the issue but necessary once `manager_cmd` changed signature. |
| `ConfigError` at execution boundary doesn't abort the pass | `factory/manage.py:279-280`; test `tests/test_factory.py:790-803` | Met. |
| `manager_failed` events excluded from round arithmetic / escalation counts, counted separately | `factory/dispatch.py:376-377`, `factory/manage.py:237-238`, `factory/stats.py:167-174`, `factory/stats.py:303`; documented `README.md:144-146` | Met; README documents the semantics so `factory stats` consumers aren't surprised. |

## Standards check

- No AGENTS.md/CONTRIBUTING.md at this head. README review conventions (`path:line` citations, acceptance-criterion-backed required fixes) apply to this review, not the diff.
- CHANGELOG updated under Unreleased (`CHANGELOG.md:4-6`), matching the existing release-note convention.
- Template/README comments on `[review]` and `[manager]` are consistent with each other (`factory/templates/factory.toml:51`, `:63-64`; `README.md:217-221`).

## Non-blocking observations

- `factory/manage.py:273`: `proc.stderr.splitlines()` assumes `stderr` is a `str`; the pre-change code (`proc.stderr or proc.stdout`) and the passing test indicate `dispatch.run` captures text, so this holds.
- `factory/manage.py:264`: `manager-prompt-<n>.md` persists under `.factory/` (gitignored). Acceptable as forensic evidence; `learn.py` instead uses a self-deleting temp file. Inconsistency is cosmetic.

No findings fail an acceptance criterion or a documented convention.

VERDICT: APPROVE

# Review: #62 — manager prompt-file transport

## Spec coverage (issue #62 "After this change" / exit gate)

| Criterion | Evidence | Status |
|---|---|---|
| `manager_cmd(prompt_path, cwd)` expands `{prompt}` with a path under `<factory>/manager-prompt-<n>.md` | `factory/config.py:146-151`; `factory/manage.py:264-266` writes `cfg.factory / f"manager-prompt-{n}.md"` | Met (write moved to caller; see Optional 1) |
| Bare `{prompt}` for `omp` → `ConfigError` naming `@{prompt}`; other programs not second-guessed | `factory/config.py:148-149` (`Path(argv[0]).name == "omp"`) | Met |
| Default manager command becomes worker shape | `factory/templates/factory.toml:63-65`; `README.md:219-220` | Met for template/README; `DEFAULT_MANAGER` constant named in "Touches" does not appear in the diff — `[INFERENCE]` it may not exist at this head; not citeable |
| `factory doctor` `manager command` row: WARN unset / FAIL placeholder or executable / PASS | `factory/onboard.py:226-238` | Met; also FAILs omp bare `{prompt}` (consistent with config rejection) |
| Nonzero exit → `Manager command exited N (argv: …unexpanded…)` + ≤5 stderr lines; `escalate` event `reason: manager_failed` | `factory/manage.py:268-274` (`json.dumps(cfg.manager)` is unexpanded argv; `splitlines()[-5:]`) | Met |
| Existing inline-text host configs: `ConfigError` contained per ticket, not aborting the pass | `factory/manage.py:279` catches `config.ConfigError`; test `tests/test_factory.py:792-804` | Met (round-1 reviewer fix) |
| `manager_failed` events don't inflate rounds/escalations | `factory/dispatch.py:376-377`, `factory/manage.py:237-238`, `factory/stats.py:167-174` | Met (round-1 reviewer fix); documented `README.md:144-146` |
| Exit-gate tests: `@<path>` readable with prompt content; ConfigError regex; HUMAN body prefix + 5-line cap; doctor WARN/FAIL | `tests/test_factory.py:150-165`, `:753-771`, `:410-436`; end-to-end District-shaped command `:730-749` | Met |
| `factory learn` also moved to file transport | `factory/learn.py:63-68` | Beyond brief but necessary: `learn.py` was the other caller of `manager_cmd` and would have broken on the signature change |
| Docs: README §Configuration + init template document file-vs-text contract | `README.md:97-106`, `:219-220`; `factory/templates/factory.toml:24-25`, `:51`, `:63-64`; `CHANGELOG.md:4-6` | Met |

Out-of-scope items (decision semantics, packet format, model) untouched.

## Required fixes

None.

## Optional suggestions

1. **Responsibility split vs. issue wording.** Issue says `manager_cmd` "writes the prompt"; the diff has callers write (`factory/manage.py:264-265`, `factory/learn.py:65-67`) and `manager_cmd` only expand. The issue's own signature (`prompt_path`) implies the caller writes, so the diff resolves the issue's internal inconsistency sensibly and the exit-gate test shape (`tests/test_factory.py:150-161`) is satisfied. No change requested; noting the deviation for the record.
2. **`learn` evidence may surface `manager_failed` as an "escalation reason."** `README.md` describes `factory learn` reading "escalation reasons"; `factory/manage.py:273-274` records those failures as `escalate` events. `stats`/`dispatch`/`manage` filter them (`factory/stats.py:167-168`, `factory/dispatch.py:376-377`, `factory/manage.py:237-238`); `learn.py` is not shown filtering. `[INFERENCE]` lessons could be generated from a tooling failure rather than a ticket failure. Low impact; the brief did not ask for this filter.
3. **`factory/manage.py:264` does not `mkdir` `cfg.factory`** while `factory/learn.py:64` does. In `manage_pass` the directory necessarily exists (escalation packet under `.factory/escalations/` is a precondition at `:239`), so this is not a defect — just asymmetric.
4. **Prompt file persists after the run** (`factory/manage.py:264-265`), unlike `learn.py`'s `NamedTemporaryFile`. Useful forensically (test `tests/test_factory.py:749` relies on it) and mirrors the worker's kept `.factory-prompt.md`; mentioning only because a 128 KiB+ packet per ticket accumulates in `.factory/`.

No net-new abstractions introduced; the `manager_failed` reason filter is a three-site predicate, not a helper, and is required to keep `escalation_count`/round arithmetic correct after the new event type.

VERDICT: APPROVE

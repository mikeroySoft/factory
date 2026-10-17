Stderr is always a captured text string; `splitlines()` is safe.

## Standards

No `AGENTS.md`/`CONTRIBUTING.md` in the repo; `docs/` holds plans, not coding rules; `pyproject.toml` only sets `ruff` `line-length = 100` (format-only; no `E501` selected). CI runs `python -m unittest discover -s tests`.

- Docs kept in sync: `README.md:97-106`, `README.md:144-146`, `factory/templates/factory.toml:25`, `:63-65`, `CHANGELOG.md:4-6` all describe the new transport, the `@{prompt}` rule, and `manager_failures`. Matches existing style of documenting `{prompt}` semantics per table.
- Event convention followed: `factory/manage.py:273-274` records `escalate` with `reason=` like `dispatch.py:387`/`:591`; all three readers of `escalate` events (`dispatch.py:376`, `manage.py:237-238`, `stats.py:167-174`) are updated; `learn.py:39` is unaffected (ticket already escalated).
- Smell (judgement call, Duplicated Code): the `Path(argv[0]).name == "omp" and "{prompt}" in argv` check appears in `factory/config.py:149` and `factory/onboard.py:234`. Doctor could call `cfg.manager_cmd(Path("x"), Path("y"))` in a `try/except ConfigError` to get the same message for free. Optional.
- Lines >100 chars in `factory/config.py:150`, `learn.py:65`, `manage.py:268`, `onboard.py:226`, `:237` match pre-existing style (`manage.py:218`, old `:264`); no lint rule enforces it. Not a finding.

## Spec

Acceptance criteria vs diff:

| Criterion | Evidence |
|---|---|
| `manager_cmd(prompt_path, cwd)` expands `{prompt}` with a file path; file at `<factory>/manager-prompt-<n>.md` | `factory/config.py:146-151`; `factory/manage.py:264-266` writes the file. Spec text says `manager_cmd` writes it, but the signature in the same sentence takes a path; end behavior identical and covered by `tests/test_factory.py:730-749`. |
| Bare `{prompt}` + argv[0] `omp` → `ConfigError` naming `@{prompt}`; other programs untouched | `config.py:149-150` (`Path(argv[0]).name` handles absolute `omp`); `tests/test_factory.py:150-165` |
| Nonzero exit → `Manager command exited N (argv: …unexpanded…)` + ≤5 stderr lines, `escalate` event `reason: manager_failed` | `manage.py:267-274`; `tests/test_factory.py:751-776` |
| Doctor `manager command` row: PASS / FAIL naming placeholder or exe / WARN when unset | `onboard.py:226-238`; `tests/test_factory.py:410-436` |
| README + template document file for workers and manager, text for reviewer | `README.md:97-101`, `factory.toml:51`, `:63-65` |
| `DEFAULT_MANAGER` becomes worker shape | No such constant exists (`config.py:105` default is `None`); template/README examples use `@{prompt}`. Satisfied in substance. |
| `factory learn` (uses `manager_cmd`) still works | `learn.py:64-68` switched to a temp file; not in brief but required by the signature change. |

Approved scope from the round-1 escalation comment (catch `ConfigError` per ticket; exclude `manager_failed` from round arithmetic): `manage.py:279`, `dispatch.py:375-378`, `stats.py:167-174`. Correct: a manager failure does not consume a round (`escalation_packet` round 2 asserted at `tests/test_factory.py:769-770`), and the next pass skips the same round because the `manage` event exists (`manage.py:242-244`), so no replay/duplicate comment (`tests/test_factory.py:778-790`).

Side effect worth knowing, not a defect: `dispatch.record("escalate", …)` inside the `manage` lifecycle scope sets `execution.outcome = "project_escalation"`, `reason = "manager_failed"` (`dispatch.py:245-248`). Lifecycle docs treat escalation as a completed mechanism, so this is consistent.

Scope creep: none. Out-of-scope items (decision semantics, packet format, model) untouched.

## Required fixes

None.

## Optional suggestions

1. `factory/onboard.py:234` duplicates `config.py:149`; reuse `manager_cmd` under `try/except ConfigError` to keep the omp rule in one place.
2. `tests/test_factory.py:153-162` writes the prompt file itself, then asserts its content — tautological for `manager_cmd`, which never writes. The real coverage is `tests/test_factory.py:730-749`; the first assertion block could shrink to the `@<path>` and `ConfigError` checks.
3. `factory/manage.py:269` only tails stderr; CLIs that print errors to stdout yield a bare one-liner. Spec says stderr, so fine as is; `proc.stderr or proc.stdout` would preserve the old fallback at no cost.

Summary: Standards 0 hard / 1 judgement-call smell; Spec 0 missing / 0 wrong.

VERDICT: APPROVE

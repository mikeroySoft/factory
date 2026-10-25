## Standards

No `AGENTS.md`/`CONTRIBUTING.md` in the snapshot; `README.md` and `docs/` document behavior, not coding rules. No documented-rule breaches found.

Smell-baseline judgement calls (non-blocking):
- **Duplicated Code** — the `omp` + bare `{prompt}` check lives in both `factory/config.py:149` and `factory/onboard.py:234`. Doctor could call `cfg.manager_cmd(Path("x"), Path("y"))` inside `try/except ConfigError` and reuse the one message; two copies will drift. Optional.
- `factory/config.py:147` docstring says "writes" nothing — the method only expands; caller writes (`factory/manage.py:263-264`, `factory/learn.py:65-67`). Harmless, matches issue text loosely.

## Spec (issue #62 + agent brief)

Acceptance criteria vs diff:
- `manager_cmd(prompt_path, cwd)` expands `{prompt}` with a path; prompt written to `<factory>/manager-prompt-<n>.md` — `factory/config.py:146-151`, `factory/manage.py:263-265`. ✔
- Bare `{prompt}` for `omp` argv[0] → `ConfigError` naming `@{prompt}`; other programs untouched — `factory/config.py:149-150` (`Path(argv[0]).name` also covers absolute paths). ✔ Test `tests/test_factory.py:150-166`. ✔
- Nonzero exit posts `Manager command exited N (argv: <unexpanded>)` + last 5 stderr lines, records `escalate` with `reason: manager_failed` — `factory/manage.py:266-273`; test `tests/test_factory.py:751-783` asserts exactly 5 trailing lines. ✔
- Doctor row: PASS/FAIL naming placeholder or executable/WARN when unset — `factory/onboard.py:226-238`; test `tests/test_factory.py:410-436`. ✔
- README + template document file-for-workers/manager, text-for-reviewer; default manager example uses `@{prompt}` — `README.md:97-100, 115-116`, `factory/templates/factory.toml:25, 56-59`. ✔ (No `DEFAULT_MANAGER` constant exists in the snapshot; `manager=None` disables. The documented example is the effective default — acceptable.)
- Reviewer round-1 follow-ups (per issue comment): `ConfigError` contained per ticket (`factory/manage.py:278`), `manager_failed` excluded from round arithmetic (`factory/dispatch.py:375-378`), both with regressions (`tests/test_factory.py:785-797`, `:771-783`). ✔

Observations, non-blocking:
- `factory/stats.py:167-171` (unchanged) counts every `escalate` event into `escalation_count`/`escalations_per_week`; the new `manager_failed` event (`factory/manage.py:272`) therefore inflates those KPIs by one per manager failure. The issue explicitly asked for an `escalate` event "so `factory stats` can count it", so this is per spec; flag if the intent was a separate counter rather than inflating escalation rate.
- `dispatch.record("escalate", …)` inside the `manage` lifecycle scope sets the manage execution's outcome to `project_escalation`/`manager_failed` (`factory/dispatch.py:245-248`, unchanged). Plausibly desired; noting the side effect.
- Scope creep: `factory/templates/factory.toml:44,48` rewrite the `ci-fix`/`conflict` example commands from `"Read the ticket prompt at {prompt}. <role-specific instructions>"` to bare `@{prompt}`. The old form was already valid file transport (path embedded in inline text) and not rejected; the rewrite drops the flake-evidence / no-semantic-change guidance, and `when` text never reaches the worker (`worker_when` only feeds manager/dashboard). Commented-out examples, so no runtime impact — optional: restore the instructions, e.g. `"@{prompt}"` plus a second inline arg, or leave the lines as they were.

## Plan drift

Plan status not recorded; drift judged against the issue's "Touches" list.
- Files changed beyond the list: `factory/dispatch.py` (round arithmetic, requested in review round 1) and `factory/learn.py` (`manager_llm` must follow the new `manager_cmd` signature — required by the cutover). `factory/doctor.py` in the issue maps to `factory/onboard.py:doctor` in this repo. No unexplained files.
- Planned proof missing from gate report: the issue's manual exit-gate step (re-run `factory manage` on District #26's kept packet with the host's command and get a real `DECISION`) is not in the gate report; the handoff says a prior attempt did it and this revision did not repeat it. Not a gate check and does not expose a code defect.

## Required fixes

None.

## Optional suggestions

1. `factory/onboard.py:234` — reuse `config.manager_cmd`'s check instead of a second copy of the omp rule.
2. `factory/templates/factory.toml:44,48` — keep the role-specific instructions in the `ci-fix`/`conflict` examples (they were valid file-transport usage).
3. Confirm the intended semantics of `manager_failed` events feeding `escalations_per_week` (`factory/stats.py:167-171`).

VERDICT: APPROVE

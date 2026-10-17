## Standards

No repo-level standards file exists (no `AGENTS.md`/`CONTRIBUTING.md`; `docs/` are plans). Only documented tooling rule is `[tool.ruff] line-length = 100` (`pyproject.toml`), which CI does not enforce and `origin/main` already violates widely, so not reported.

Required: none.

Optional (judgement calls):
- **Duplicated Code** — the omp bare-`{prompt}` rule lives twice: `factory/config.py:149-150` and `factory/onboard.py:234-235`. Doctor could call `cfg.manager_cmd(Path("x"), Path("."))` inside `try/except ConfigError` and reuse the message. Two sites with one rule will drift; not blocking.
- `factory/manage.py:273-274` records `escalate` inside the `manage` lifecycle scope, so `dispatch.record` (`factory/dispatch.py:245-248`) flips the manage execution's `outcome` to `project_escalation` / `reason=manager_failed`. No reader consumes execution outcome today (grep: `evidence.py`/`briefing.py`/`dashboard.py` do not), so no behavioural effect — note only.
- `tests/test_factory.py:150-161` writes the prompt file itself then asserts it is readable with the same text; the readable-file assertion is tautological for `manager_cmd` (which only expands). The CLI test at `tests/test_factory.py:730-751` is the one that actually proves the transport. Fine to leave.

## Spec (issue #62 + agent brief)

Acceptance criteria vs diff:
- `manager_cmd(prompt_path, cwd)` expands `{prompt}` with a path to `<factory>/manager-prompt-<n>.md` — `factory/config.py:146-151`, `factory/manage.py:264-266`. The file write sits in the caller rather than `manager_cmd`; observable behaviour (path, content, argv `@<path>`) matches the exit gate. All callers migrated (`factory/learn.py:64-68`). No `DEFAULT_MANAGER` constant exists on `origin/main`; the documented examples switch to `@{prompt}` (`README.md:220`, `factory/templates/factory.toml:63-65`). ✓
- Bare `{prompt}` for omp → `ConfigError` naming `@{prompt}`, other programs untouched — `factory/config.py:149-150`; exact-element check correctly leaves the inline-text conflict command (`factory/templates/factory.toml:51`) alone. ✓
- Nonzero exit posts `Manager command exited N (argv: <unexpanded>)` + last 5 stderr lines, records `escalate` with `reason: manager_failed` — `factory/manage.py:267-274`. ✓ Tested `tests/test_factory.py:753-786`.
- `factory doctor` `manager command` row: WARN unset, FAIL missing placeholder/executable, PASS otherwise — `factory/onboard.py:226-238`; tested `tests/test_factory.py:410-436`. ✓
- README §Configuration + init template document file for workers/manager, text for reviewer — `README.md:97-106`, `factory/templates/factory.toml:25,51,63-65`. ✓
- Approved follow-ups from review round 1 (issue comment 2026-09-09): `ConfigError` contained per ticket without replay (`factory/manage.py:279-280`, test `:788-801`); `manager_failed` excluded from round arithmetic (`factory/dispatch.py:375-378`) and from manager selection (`factory/manage.py:237-238`); stats count failures separately (`factory/stats.py:167-174,303`). ✓
- Out of scope respected: decision semantics, packet format, model unchanged.

Missing/partial: none. Scope creep: none beyond the approved review-round changes; CHANGELOG entry (`CHANGELOG.md:4-6`) matches existing `(#N)` format.

Summary — Standards: 0 required, 3 optional (worst: duplicated omp rule). Spec: 0 required, 0 optional.

VERDICT: APPROVE

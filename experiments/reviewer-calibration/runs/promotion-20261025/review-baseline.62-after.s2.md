## Standards

No `AGENTS.md`/`CONTRIBUTING.md` in the snapshot; documented conventions are `README.md` (CHANGELOG referenced at README:50), `pyproject.toml` (`[tool.ruff] line-length = 100`, not enforced by CI or `.factory.toml` gate), and the existing code patterns.

- `factory/config.py:149` and `factory/onboard.py:188` (diff 84, 188) both implement the same `Path(argv[0]).name == "omp" and "{prompt}" in argv` test with two different messages. Possible Duplicated Code — judgment call, not a documented-rule breach. Optional: doctor could call `cfg.manager_cmd(Path("x"), Path("y"))` and report the `ConfigError` text, so the two surfaces can't drift.
- `factory/manage.py:273` (diff 159–160): `dispatch.record("escalate", …)` inside the `manage` lifecycle scope flips `execution.outcome` to `project_escalation` (dispatch.py:245–248). The sibling failure path at manage.py:293 classifies a failing mechanism as `mechanism_failure`; per lifecycle.py:591–592 only that outcome surfaces as failed. A crashed manager command is observed as a normal escalation. Optional: set `execution.outcome = "mechanism_failure"`, `execution.reason = "manager_failed"` after the record. Not blocking: the spec mandates the `escalate` event, and no documented rule names the outcome.
- `factory/manage.py:269` (diff 155): tail is stderr-only; the old body fell back to stdout. CLIs that print errors on stdout now yield a bare `Manager command exited N (argv: …)`. Matches the spec's "last 5 stderr lines" literally; optional to fall back to the stdout tail when stderr is empty.

## Spec (issue #62 + agent brief)

| Criterion | Evidence | Status |
|---|---|---|
| `manager_cmd(prompt_path, cwd)` expands `{prompt}` with a path; prompt lands at `<factory>/manager-prompt-<n>.md` | config.py:81–86; manage.py:149–150 | Met (file written by `manage.py`, not `manager_cmd`; observable contract identical) |
| README §Configuration + `factory init` template: `{prompt}` = file for workers and manager, text for reviewer; example manager command uses `@{prompt}` | README.md:38–42, 65–66; templates/factory.toml:232, 241, 251–253 | Met |
| Bare `{prompt}` for `omp` → `ConfigError` naming `use "@{prompt}"`; other programs not second-guessed | config.py:84–85 | Met |
| Nonzero exit posts `Manager command exited N (argv: …unexpanded…)` + last 5 stderr lines | manage.py:154–157 | Met |
| Records `escalate` event with `reason: manager_failed`; `factory stats` counts it | manage.py:159–160; stats.py:211–212, 220 | Met |
| Doctor `manager command` row: PASS / FAIL (names placeholder or executable) / WARN when unset | onboard.py:180–192 | Met |
| Exit-gate tests: `@<path>` readable + bare-omp ConfigError; HUMAN body prefix ≤5 stderr lines; doctor WARN/FAIL | tests/test_factory.py:265–280, 298–324, 353–372 | Met |
| `DEFAULT_MANAGER` becomes worker shape | No `DEFAULT_MANAGER` exists in `config.py` (manager defaults to `None`, config.py:105); documented example updated instead | N/A |
| Out of scope untouched (decision semantics, packet format, model) | — | Respected |

Approved scope change (issue comment, review round 1): `dispatch.py:97–101` and `manage.py:139–140` exclude `manager_failed` events from round arithmetic; `manage.py:166` catches `ConfigError` so a bad config yields a HUMAN diagnosis instead of aborting the pass. Both covered by tests (diff 382–390, 392–404).

Caller migration complete: `learn.py:122–126` is the only other `manager_cmd` caller and now passes a temp file; `ConfigError` there propagates as `SystemExit` (config.py:70), acceptable for a CLI.

## Plan drift

Plan not recorded; compared against the issue's "Touches" list.

- Files not named: `CHANGELOG.md` (README:50 points users at it; appropriate), `factory/dispatch.py` (approved round-1 fix), `factory/learn.py` (required caller migration for the signature change), `factory/stats.py` (needed for "so `factory stats` can count it"). None exposes a defect.
- Planned proof missing from the gate report: the manual exit-gate item "re-run `factory manage` on District #26's kept packet … get a real DECISION line". The handoff claims a prior attempt observed `DECISION: FIX` (`.factory/district-26-replay.log`); not reproducible from the gate report, not re-run in this revision. Unverified, not a defect in the diff.

## Required fixes

None.

## Optional suggestions

1. `factory/manage.py:273`: mark the manage execution `mechanism_failure`/`manager_failed` after recording the event (mirrors manage.py:293).
2. `factory/manage.py:269`: fall back to the stdout tail when stderr is empty.
3. `factory/onboard.py:188`: derive the omp check from `manager_cmd` instead of duplicating it.

VERDICT: APPROVE

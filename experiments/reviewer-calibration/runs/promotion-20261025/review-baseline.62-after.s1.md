## Review: #62 — manager prompt transport

### Standards
No `AGENTS.md`/`CONTRIBUTING.md` in the snapshot; the only documented conventions are CI (`.github/workflows/ci.yml:11` → `unittest discover`, gate PASS) and `CHANGELOG.md` linked from `README.md:50`. Diff adds an `Unreleased` entry (`CHANGELOG.md:8-10`) and updates both documentation surfaces named in the brief (`README.md:38-47,65-66`, `factory/templates/factory.toml:232,251-253`). Compliant.

### Spec (issue #62 "After this change" + exit gate)
| Criterion | Evidence | Status |
|---|---|---|
| `manager_cmd(prompt_path, cwd)` expands `{prompt}` with a file at `<factory>/manager-prompt-<n>.md` | `factory/config.py:81-86`, `factory/manage.py:149-151` | ✓ (file written by caller, same split as the worker transport) |
| Default/template manager shape `@{prompt}` | `README.md:66`, `factory/templates/factory.toml:253` | ✓ (no `DEFAULT_MANAGER` constant exists in base; unset = disabled, as before) |
| Bare `{prompt}` + argv[0] `omp` → `ConfigError` naming `@{prompt}`; other programs untouched | `factory/config.py:84-85` (exact-element match, so `@{prompt}` passes; basename check) | ✓ |
| Doctor row: PASS / FAIL (placeholder, executable) / WARN (unset) | `factory/onboard.py:180-192`; `report(None, …)` → WARN per `onboard.py:168` | ✓ |
| Nonzero exit → `Manager command exited N (argv: …unexpanded…)` + last 5 stderr lines, `escalate` event `reason: manager_failed` | `factory/manage.py:154-160` | ✓ |
| `factory stats` can count it | `factory/stats.py:205-212,220` | ✓ |
| Tests: `@<path>` readable content; ConfigError on bare `{prompt}`; HUMAN body prefix + ≤5 stderr lines; doctor WARN/FAIL | `tests/test_factory.py:265-280, 298-324, 353-372` | ✓ |
| Manual District #26 replay | not in gate report; handoff states a prior attempt did it, this revision did not | not re-verified (manual item, non-blocking) |

Consequential changes beyond the literal brief are necessary, not speculative: `factory/learn.py:122-126` is the second `manager_cmd` caller and would otherwise pass text where a `Path` is expected; `factory/dispatch.py:98-100` and `factory/manage.py:139-140` keep the new `manager_failed` event from inflating round numbers / being picked as "the escalation" (verified against the only other `escalate` consumers: `learn.py:39` unaffected since a `manager_failed` row implies a prior real escalation; `dashboard.py:635` reads `escalation_count`, which excludes it).

### Plan drift
Plan status not recorded; no plan file to diff against. Relative to the issue's "Touches" list, extra files: `CHANGELOG.md`, `factory/dispatch.py`, `factory/learn.py`, `factory/stats.py` — all justified above. Planned proof missing from the gate report: the manual District #26 packet replay. Exposes no defect.

### Required fixes
None.

### Optional suggestions
- `factory/manage.py:166-167` — `OSError`/`ConfigError` failures post HUMAN but record no `manager_failed` event, so a missing executable or bad omp shape never shows in `manager_failures`. Spec only mandates the nonzero-exit path; if the counter is meant to cover "manager didn't run", record the same event here.
- `factory/manage.py:159-160` — `dispatch.record("escalate", …)` inside the `manage` lifecycle scope flips the execution's terminal outcome to `project_escalation`/`manager_failed` (`dispatch.py:245-248`), whereas `lifecycle.py:591-592` reserves `mechanism_failure` for runtime failures. Cosmetic in the journal; no consumer in the diff depends on it.
- `factory/manage.py:154` — `json.dumps(cfg.manager)` is posted verbatim to the issue comment. Spec asked for it, but host argv can carry local paths or `--api-key`-style secrets; worth a note in the README line that documents the body shape if public repos are a target.
- `factory/onboard.py:188-189` duplicates the omp check in `config.py:84-85`. Two lines; fine to leave.

VERDICT: APPROVE

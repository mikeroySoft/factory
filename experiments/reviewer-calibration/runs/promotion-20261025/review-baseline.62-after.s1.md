## Standards

Sources: `README.md` ("No Python dependencies"), `.github/ISSUE_TEMPLATE/agent_task.md`, `CHANGELOG.md` format. No `CONTRIBUTING.md`/`AGENTS.md` in snapshot.

- Stdlib only (`tempfile.NamedTemporaryFile`, `json`) — `review.diff:112`, `review.diff:154`. Compliant.
- CHANGELOG entry under `## Unreleased` with `(#62)` suffix matches existing entry style — `review.diff:8-10`.
- Template/README docs updated in step with behavior — `review.diff:38-47`, `review.diff:65-66`, `review.diff:251-253`.
- Smell baseline: the `reason != "manager_failed"` filter is repeated in four hunks (`review.diff:99`, `:139-140`, `:205-206`, `:211`). Judgement call (Duplicated Code); each site is a one-line predicate and a shared helper would be a net-new abstraction — not warranted.

No documented-standard violations.

## Spec (issue #62 + agent brief)

| Criterion | Evidence | Status |
|---|---|---|
| `manager_cmd(prompt_path, cwd)` expands `{prompt}` with a file path; prompt written to `<factory>/manager-prompt-<n>.md` | `review.diff:81-86`, `review.diff:149-151` | Met |
| Bare `{prompt}` for `omp` argv[0] → `ConfigError` naming `use "@{prompt}"`; other programs not second-guessed | `review.diff:84-85` (`Path(argv[0]).name == "omp"`) | Met |
| Default/template manager command is worker shape (`@{prompt}`) | `review.diff:66`, `review.diff:253` (no `DEFAULT_MANAGER` exists in repo; manager is opt-in) | Met |
| README + template document `{prompt}` = file for workers/manager, text for reviewer | `review.diff:38-42`, `review.diff:231-232`, `review.diff:241`, `review.diff:251` | Met |
| Nonzero exit posts `Manager command exited N (argv: …unexpanded…)` + last 5 stderr lines | `review.diff:154-158` | Met |
| Records `escalate` event with `reason: manager_failed` | `review.diff:159-160` | Met |
| `factory doctor` `manager command` row: PASS/FAIL (placeholder or executable)/WARN (unset) | `review.diff:180-192` (`report(None, …)` → WARN per `onboard.py:168`) | Met |
| Exit-gate tests: `@<path>` readable content; ConfigError on bare omp `{prompt}`; HUMAN body ≤5 stderr lines; doctor WARN/FAIL | `review.diff:265-280`, `review.diff:298-324`, `review.diff:353-372` | Met |
| Out of scope untouched (decision semantics, packet format, model) | no changes to `parse`/`MENU`/packet builder | Met |

Consequential fixes beyond the brief, required for correctness once a second `escalate` event exists per round: round arithmetic (`review.diff:97-101`), manager pass selection (`review.diff:139-140`), stats counts (`review.diff:204-212`). Without these a failed manager would consume a round and inflate `escalation_count`. Approved in review round 1 per the issue's handoff comment.

Caller migration: `learn.py` (`review.diff:122-126`) adapts the only other `manager_cmd` caller; `NamedTemporaryFile` scope encloses `dispatch.run`, so the file outlives the subprocess.

## Plan drift

Plan not recorded. Files changed not named in the issue's "Touches": `CHANGELOG.md`, `factory/dispatch.py`, `factory/learn.py`, `factory/stats.py` (`factory/doctor.py` named; doctor lives in `onboard.py`). Each is a forced consequence of the named changes (signature migration, new event kind, changelog convention). None exposes a defect.

Proof not in gate report: issue exit-gate item "Manual: re-run `factory manage` on District #26's kept packet". Handoff comment claims a prior attempt did this (`.factory/district-26-replay.log`, `DECISION: FIX`); not repeated this revision and not in the gate report. `test_manager_reads_prompt_file_with_district_command` (`review.diff:332-351`) exercises the District-shaped command with a ~200 KB prompt as the automated proxy. Unverified, not a code defect.

## Required fixes

None.

## Optional suggestions

- `review.diff:166-167`: the `ConfigError` path (bare omp `{prompt}`) posts a HUMAN comment per escalated ticket but records no `manager_failed` event, so `manager_failures` undercounts misconfiguration. Spec only mandates counting nonzero exits; mention for stats fidelity.
- `review.diff:159-160`: `dispatch.record("escalate", …)` inside the `manage` lifecycle scope sets that execution's exit `outcome` to `project_escalation` / `reason=manager_failed` (`dispatch.py:245-248`). Lifecycle maps it to `completed` state, so no behavioral effect; noted for anyone reading `manage` execution journals.
- `review.diff:192`: on PASS the detail shows only `argv[0]`; showing the full argv would let operators confirm the `@{prompt}` shape at a glance.

VERDICT: APPROVE

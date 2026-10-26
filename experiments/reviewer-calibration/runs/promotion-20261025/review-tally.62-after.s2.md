## Standards

No `AGENTS.md`/`CONTRIBUTING.md` in the snapshot; `docs/` holds plans, not coding rules. README conventions followed: `{prompt}` semantics documented for workers/manager/reviewer (README.md:38-42, template factory.toml:231-232, 251-253), CHANGELOG entry added under a new `## Unreleased` heading (CHANGELOG.md:8-10; no prior convention, acceptable). Existing patterns reused: `expand()`, `dispatch.record`, `report()` in doctor, `human_touch` dict. No new abstractions.

## Spec (issue #62 + agent brief)

- `manager_cmd(prompt_path, cwd)` expands a path; `manage.py:149-151` writes `<factory>/manager-prompt-<n>.md`. Test asserts `@<path>` readable with 30k-word content (test_factory.py:265-280). ✔
- Bare `{prompt}` for `omp` → `ConfigError` naming `@{prompt}` (config.py:84-85); other programs untouched. ✔ ConfigError is a `SystemExit` subclass but is caught explicitly at manage.py:166, so a stale host config yields a HUMAN diagnosis instead of aborting the pass. ✔
- Nonzero exit → `Manager command exited N (argv: …unexpanded…)` + ≤5 stderr lines (manage.py:154-158); `escalate` event with `reason: manager_failed` (manage.py:159-160); counted in `stats` (stats.py:211-212) and excluded from round arithmetic (dispatch.py:99-100, manage.py:139-140, stats.py:205-206). Traced: next real escalation gets round 2 after a round-1 failure (test:382-390). ✔
- Doctor row: WARN unset / FAIL missing placeholder or executable / PASS (onboard.py:180-192). ✔ Issue named `factory/doctor.py`; doctor lives in `onboard.py` — correct location.
- `DEFAULT_MANAGER` named in the brief does not exist in the codebase (grep: no hits); the template/README default now uses `@{prompt}`. ✔
- `factory learn` (second caller of `manager_cmd`) migrated to a file prompt (learn.py:122-126). ✔ Required by the signature change; not drift.
- Out-of-scope items (decision semantics, packet format, model) untouched. ✔

## Plan drift

Plan not recorded; measured against the issue "Touches" list.
- Files changed not named: `CHANGELOG.md`, `factory/dispatch.py`, `factory/learn.py`, `factory/stats.py`. All trace to acceptance criteria (round arithmetic, stats counting, second `manager_cmd` caller) or changelog convention. No defect exposed.
- Proof missing from gate report: the issue's manual exit gate (re-run `factory manage` on District #26's kept packet). Handoff says a prior attempt did it (`DECISION: FIX`) and this revision did not repeat it; the gate report covers only conflict-markers/test/leak-scan. Not a required defect.

```json
{"verdict":"APPROVE","findings":[
{"severity":"nit","pass":"bug","path":"factory/manage.py","line":159,"summary":"dispatch.record('escalate', reason='manager_failed') runs inside the `manage` lifecycle scope, so dispatch.record (dispatch.py:245-248) flips the manage execution's outcome to `project_escalation` with reason `manager_failed`, which is not in runtime_events.REASONS (runtime_events.py:34-44) and is normalized to None by _reason(). Runtime evidence for a manager crash reads as a project escalation with no reason rather than a mechanism failure. Consider setting outcome/reason explicitly or adding the reason to the vocabulary."},
{"severity":"nit","pass":"security","path":"factory/manage.py","line":154,"summary":"json.dumps(cfg.manager) posts the full unexpanded manager argv to the GitHub issue comment via apply(). Spec-requested, but any secret passed as a manager CLI argument (e.g. --api-key) would be published to the issue. README could warn operators to keep credentials in env, not argv."}
]}
```

VERDICT: APPROVE

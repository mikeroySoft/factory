**Standards** — No AGENTS.md/CONTRIBUTING.md in repo; README is the documented contract. README §Configuration, the `factory init` template, and CHANGELOG `Unreleased` are updated consistently with the new transport. One contract violation found (below).

**Spec (issue #62 AC trace)**
- File transport: `manage.py:264-266` writes `<factory>/manager-prompt-<n>.md`, `config.py:146-151` expands the path; `learn.py:64-68` migrated too. ✓
- Bare `{prompt}` for `omp` → `ConfigError` naming `@{prompt}` (`config.py:149-150`), `Path(argv[0]).name` handles absolute paths; other programs not second-guessed. ✓ Caught in `manage.py:279` so a stale host config yields a HUMAN diagnosis without aborting the pass. ✓
- Doctor row WARN/FAIL/PASS (`onboard.py:226-238`). ✓
- Nonzero exit body: `Manager command exited N (argv: <unexpanded>)` + last 5 stderr lines (`manage.py:268-271`). ✓
- `escalate` event with `reason: manager_failed` recorded (`manage.py:273-274`); excluded from round arithmetic (`dispatch.py:376`, `manage.py:237-238`) and from `escalation_count`, counted as `manager_failures` (`stats.py:167-174`); every stats/dashboard row goes through `human_touch` so the key is always present. ✓
- Human takeover: the failure row is written before `human_activity` re-check, but the next pass still resolves the original escalation and skips on human activity. ✓

**Defect**: `dispatch.record("escalate", …)` has a side effect the diff did not account for — `dispatch.py:245-248` stamps the *current* lifecycle execution (`manage` scope, `manage.py:225`) with `outcome="project_escalation"`, `reason="manager_failed"`. README:372 defines `project_escalation` as "the existing project escalation path ran; not a broken runtime mechanism"; README:376 classifies "unexplained worker/reviewer nonzero exits, command/API errors" as `unknown`. Trigger: any nonzero manager exit. Impact: `--runtime-json`/evidence consumers see the `manage` execution as a healthy escalation (reason nulled by `runtime_events._reason`) — the exact failure this issue exists to surface. One-line fix after the record call: `execution.outcome, execution.reason = "unknown", f"manager_exit:{proc.returncode}"` (or record the row without mutating the scope).

**Plan drift** — No plan recorded. Files beyond the issue's "Touches": `CHANGELOG.md`, `factory/dispatch.py`, `factory/learn.py`, `factory/stats.py` — all forced by the `manager_cmd` signature change or the `manager_failed` AC; no drift defect. Issue exit-gate "Manual: re-run `factory manage` on District #26's kept packet" is absent from the gate report (handoff attributes it to a prior attempt; unverified).

```json
{"verdict":"REVISE","findings":[
{"severity":"important","pass":"bug","path":"factory/manage.py","line":273,"summary":"dispatch.record(\"escalate\", reason=\"manager_failed\") runs inside the `manage` lifecycle scope, so dispatch.py:245-248 sets the execution outcome to `project_escalation` (README:372: \"not a broken runtime mechanism\") for a manager nonzero exit that README:376 classifies as `unknown`; runtime JSON then reports the failed manager run as a healthy escalation with reason null. Set execution.outcome/reason explicitly after recording (e.g. `unknown`, `manager_exit:N`)."},
{"severity":"nit","pass":"spec","path":"factory/manage.py","line":279,"summary":"ConfigError/OSError path posts HUMAN but records no `manager_failed` escalate row, so misconfigured or missing manager commands are invisible in `manager_failures` stats while nonzero exits are counted."}
]}
```
VERDICT: REVISE

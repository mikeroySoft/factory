```json
{
  "verdict": "REVISE",
  "findings": [
    {
      "severity": "important",
      "pass": "bug",
      "path": "factory/manage.py",
      "line": 272,
      "summary": "The `manager_failed` escalate event is recorded before the `human_activity` guard at manage.py:281 and becomes the newest `escalate` row, which manage.py:237 selects as the anchor on the next pass. Trigger: a human comments/relabels while the manager command is running and the command then exits nonzero → pass 1 `continue`s (no `manage` event), pass 2 anchors on the manager_failed row whose `at` is later than the human activity, so human_activity() (manage.py:136 skips items created before `escalation[\"at\"]`) returns False and the manager re-runs the same round, applying RETRY/REWRITE/SPLIT/ROUTE (label flip, body rewrite, child issues) to a ticket the human already took over. README:85 / docs/manager-plan.md:249 require manage to act only on untouched packets with no human activity since the escalation; pre-diff the original escalate row stayed the anchor so this could not happen. Fix: record the manager_failed event after the line-281 guard (next to the `manage` record), or anchor manage_pass on the latest escalate with `reason != \"manager_failed\"`."
    },
    {
      "severity": "important",
      "pass": "spec",
      "path": "factory/templates/factory.toml",
      "line": 44,
      "summary": "Unrequested rewrite of the `ci-fix` (line 44) and `conflict` (line 49) default worker profiles drops their role instructions (flake-with-evidence requirement, no-semantic-change rebase rule) and replaces them with a bare `@{prompt}`. The old form was not broken: `{prompt}` embedded inside inline text expands to the path and is valid for omp (the issue only rejects a bare `{prompt}` element). `[workers.<x>].when` is never delivered to the worker (dispatch.py:200-226 builds the prompt from issue, standing instructions, lessons, handoff, brief; `worker_when` is read only by manage.py:253 and dashboard.py:871), so operators who uncomment these shipped profiles (CHANGELOG:29, #20) now get a worker with no profile-specific guidance. Issue #62 scope for the template is documenting `{prompt}` = file for workers/manager; revert these two command lines."
    },
    {
      "severity": "nit",
      "pass": "bug",
      "path": "factory/manage.py",
      "line": 272,
      "summary": "dispatch.record(\"escalate\", …) inside the `manage` lifecycle scope sets `execution.outcome = \"project_escalation\"` (dispatch.py:245-248), which README:368 defines as 'the existing project escalation path ran; not a broken runtime mechanism', for what is actually a manager nonzero exit (README:372 classifies unexplained nonzero exits as `unknown`). `manager_failed` is also absent from runtime_events.REASONS (runtime_events.py:34-44), so the runtime projection shows the failed manage execution as completed/project_escalation with a null reason. Set the manage execution outcome/reason explicitly after recording, or add the reason code."
    },
    {
      "severity": "nit",
      "pass": "bug",
      "path": "factory/manage.py",
      "line": 268,
      "summary": "Only stderr is tailed; a CLI that reports its failure on stdout with empty stderr now yields a HUMAN body containing just the argv (pre-diff fell back to `proc.stderr or proc.stdout`). Consider `(proc.stderr or proc.stdout).splitlines()[-5:]`."
    }
  ]
}
```

Plan drift: no plan recorded; versus the issue's Touches list the diff additionally changes `factory/dispatch.py` (round arithmetic excluding `manager_failed`) and `factory/learn.py` (manager_llm switched to a temp prompt file) — both necessary consequences of the `manager_cmd` signature change. Missing proof: the gate report covers conflict-markers/test/leak-scan only; the issue's manual exit gate (District #26 kept-packet replay yielding a real DECISION) is reported by the handoff as done in a prior attempt and not repeated for this revision.

VERDICT: REVISE

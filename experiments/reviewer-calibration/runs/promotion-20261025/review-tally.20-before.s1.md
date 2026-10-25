```json
{
  "verdict": "REVISE",
  "findings": [
    {
      "severity": "important",
      "pass": "bug",
      "path": "factory/manage.py",
      "line": 124,
      "summary": "FIX pushes with plain `git push origin agent/<n>` after the worker round. The shipped `conflict` profile (factory/templates/factory.toml:47) instructs the worker to (re)start and resolve the rebase onto main, which rewrites agent/<n> history (the earlier `refresh_pr_branch` conflict path ran `git rebase --abort`, so remote == pre-rebase tip). A successful rebase + gate PASS then hits a non-fast-forward rejection → CalledProcessError → manage_pass logs `manager decision application failed … leaving for human` as mechanism_failure with no issue comment, no escalation, and the resolved worktree unpublished; the single manager round (manager_rounds=1) is consumed by the recorded `manage` event so it is never retried. Issue #20 scope ships `conflict` as a FIX-dispatched profile (\"resolve the rebase, keep both intents\"), and README.md:21 states FIX \"pushes only on gate PASS\"; the only dispatch path for that profile cannot publish its result. `refresh_pr_branch` (dispatch.py:731) already uses `--force-with-lease` for exactly this case; the FIX push should match it (safe for ci-fix too)."
    },
    {
      "severity": "nit",
      "pass": "spec",
      "path": "factory/manage.py",
      "line": 109,
      "summary": "FIX precondition also refuses PRs with `reviewDecision == CHANGES_REQUESTED`; README.md:18-24 and MENU (manage.py:26-28) describe FIX only as requiring a kept worktree and an open PR. Document the human-blocks-FIX rule next to the ROUTE/FIX text so the manager's HUMAN diagnosis can account for it."
    },
    {
      "severity": "nit",
      "pass": "bug",
      "path": "factory/manage.py",
      "line": 54,
      "summary": "`parse` still subtracts RESERVED_LABELS (ROUTE, line 54) and re-checks `worker in RESERVED_LABELS` (FIX, line 65) although manage_pass:199 now passes an already-filtered `workers` dict; redundant, harmless."
    }
  ]
}
```

Plan drift: issue Touches named config.py, manage.py, dashboard.py, templates/factory.toml; diff also changes README.md (documents the new decision/config) and tests/test_factory.py (required by the exit gate's `pytest tests/test_factory.py`). Planned proof `uv run pytest tests/test_factory.py` is covered by the gate's `test: PASS` (same `tests/` discovery). Neither drift exposes a defect; the REVISE rests solely on the non-force push.

VERDICT: REVISE

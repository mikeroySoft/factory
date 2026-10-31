The diff covers all four exit-gate items in issue #20. It still needs revision because of one bug: if a manager FIX gets a REVISE from the reviewer, the PR can still be merged.

**The bug.** A FIX never removes `factory-approved` from the PR before it pushes.
1. When a rebase onto a moved main conflicts, `refresh_pr_branch` escalates the ticket but leaves `factory-approved` on the PR (`dispatch.py:722-725`).
2. The manager then picks FIX with the `conflict` worker. The worker resolves the rebase, the gate passes, and FIX force-pushes (`factory/manage.py:124`) before the review runs (`:125`).
3. If the reviewer says REVISE, FIX only escalates (`:130`). The label is still there.
4. On the next pass, `land_pass` runs before `manage_pass` (`dispatch.py:1116-1119`). The merge stage only checks the PR label, CI and whether the branch is behind main (`dispatch.py:803-863`). The branch was just rebased, so once CI is green it squash-merges the rejected commits.

This breaks the README rule added in this diff ("Only a fresh APPROVE restores `factory-approved`") and the plan rule in `docs/manager-plan.md:143,284`. It also hits the `conflict` profile the issue asks for. The same gap exists between the push and the review verdict. The fix is to remove `factory-approved` before the worker round or push, as the red-CI path already does at `dispatch.py:825-837`. No new abstraction is needed.

**Spec.** Each exit-gate item has a test in the diff:
- A ROUTE to `chore` with no `chore` worker is turned into a recorded HUMAN decision; with the worker, the label is applied.
- A FIX on red CI runs the selected worker's command (`fix-worker`).
- The old array form of `[workers]` still loads.

The `ci-fix` and `conflict` profiles are in the template as commented, opt-in host config. That matches "New profiles remain host-config, human-applied" and how the rest of the template's `[workers]` is written.

**Standards.** The repo has no AGENTS.md or CONTRIBUTING.md. The rules that apply are in the README and `docs/manager-plan.md` §4.3–4.4. The only breach I found is the bug above.

**Plan drift.** No plan was recorded, so nothing planned can be compared against the gate report. Two changed files are not in the issue's "Touches" list:
- `README.md`: documentation for the new worker table and FIX.
- `tests/test_factory.py`: the tests the exit gate needs.

The diff also adds the FIX decision itself in `manage.py`, which the plan places under T10 (#19). The exit gate needs FIX, so that is not scope creep by itself, but it is the drift that contains the bug above.

```json
{"verdict":"REVISE","findings":[{"id":"F1","severity":"important","pass":"bug","class":"fix-push-keeps-stale-approval","path":"factory/manage.py","line":124,"summary":"FIX force-pushes new commits and, on REVISE, only escalates; it never removes `factory-approved`. After a rebase-conflict escalation (refresh_pr_branch leaves the label on), a conflict-worker FIX that is REVISEd stays merge-eligible. The next land_pass merges the rejected commits once CI is green, because the rebased branch is not behind main. This breaks README 'Only a fresh APPROVE restores factory-approved' and docs/manager-plan.md:143,284. Fix: remove the label before the worker round/push.","instances":[{"path":"factory/manage.py","line":124,"summary":"push of unreviewed FIX commits while a prior factory-approved label may still be on the PR"},{"path":"factory/manage.py","line":130,"summary":"REVISE path escalates without removing factory-approved"}]},{"id":"F2","severity":"nit","pass":"spec","class":"when-rules-not-rendered","path":"factory/dashboard.py","line":847,"summary":"worker_when is added to the snapshot JSON, but the dashboard config card (dashboard.html workers list) never shows it, so operators cannot see routing rules in the UI.","instances":[{"path":"factory/dashboard.py","line":847,"summary":"worker_when exported but not rendered"}]}]}
```
VERDICT: REVISE

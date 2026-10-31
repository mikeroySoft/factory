I found one Important problem, so the verdict is REVISE. The `conflict` profile added to the template fails every time it runs through FIX. When the manager sends a rebase-conflict escalation to it, the worker rebases, the gate passes, and then the plain `git push` is rejected because the rebase rewrote the branch history. The failure is only logged as a mechanism failure: nothing is posted on GitHub, and the work never reaches the open PR.

**Spec (issue #20 acceptance criteria):** all are met.
- A ROUTE to `chore` without a `chore` worker is rejected and recorded as HUMAN; with one, the label is applied.
- A FIX on red CI runs the `ci-fix` argv.
- The old array form of `[workers]` still loads, and a worker can now also be a table with `command` and an optional `when`.
- The manager prompt lists each label with its `when`.
- ROUTE and FIX only accept listed labels.

**Standards:** the repo has no AGENTS.md or CONTRIBUTING.md, and `docs/` contains only the plan and the site. The template comments and README describe the new keys.

**Plan drift:**
- No worker plan was recorded. Compared with the issue's Touches list, the diff also changes `README.md` (docs) and `tests/test_factory.py` (needed for the exit gate).
- In `manage.py` it adds a working FIX decision, which `docs/manager-plan.md` assigns to T6 (the PR frontier). This was needed: FIX did not exist at base, and the exit gate needs "FIX on red CI runs the ci-fix argv".
- The gate report shows only a passing test run, with no separate proof of the ROUTE and FIX scenarios. The new tests cover them. None of this drift exposes a required defect beyond the finding below.

```json
{"verdict":"REVISE","findings":[{"severity":"important","pass":"bug","path":"factory/manage.py","line":124,"summary":"The new FIX path pushes with a plain `git push origin agent/<n>`. The `conflict` profile added to factory/templates/factory.toml:45-47 and README.md:23 tells the worker to resolve, or restart, a rebase onto main. Rebasing rewrites the history of agent/<n>, so this push is always rejected as non-fast-forward. Trigger: the manager picks FIX {\"worker\":\"conflict\"} for a 'rebase onto moved main conflicts' escalation, which is exactly what that profile's `when` rule invites. Impact: the gate passes, the push raises CalledProcessError, and apply() is only logged as mechanism_failure. Nothing appears on the issue or PR, the manage round is used up, and the fixed branch never reaches the PR. refresh_pr_branch already uses --force-with-lease for rebased branches; FIX needs the same, or the conflict profile cannot work through FIX."},{"severity":"nit","pass":"spec","path":"factory/dashboard.py","line":847,"summary":"`worker_when` is added to the dashboard snapshot, but dashboard.html never reads it. The workers card still shows only label and command, so the field has no consumer."}]}
```
VERDICT: REVISE

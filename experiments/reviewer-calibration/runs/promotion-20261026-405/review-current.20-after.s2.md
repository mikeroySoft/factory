I found one blocking defect. It is in the new `FIX` path: it can push new code to a PR that still has the `factory-approved` label, and nothing removes that label if the review then asks for changes.

**Spec.** The diff meets the issue's exit gate.
- **Tables and `when` rules:** worker entries can now be a table with `command` and an optional `when`. Existing array entries still load (`config.py:288-298`).
- **Manager prompt and label checks:** the prompt lists each worker label with its `when` rule. The same filtered list is what `ROUTE` and `FIX` are checked against (`manage.py:199-213`, `manage.py:63-69`).
- **Rejection is recorded:** a `ROUTE chore` with no `chore` worker becomes a `HUMAN` decision and is written to the event log.
- **`FIX` runs the chosen worker:** it runs the selected worker's command in the kept worktree, and the tests cover this.
- **Default profiles:** `ci-fix` and `conflict` are shipped commented out in the template. Every other key in that template is also commented out, and `workers` is a host-owned table, so this matches "host-config, human-applied".

**Standards.** The repo has no `AGENTS.md` or `CONTRIBUTING.md`. The README is updated to match. `--force-with-lease` is the same push the existing `refresh_pr_branch` already uses.

**The blocking defect.** Two existing escalation paths in `dispatch.py` leave `factory-approved` on the PR: a rebase conflict (`dispatch.py:722-724`) and a gate failure after rebase (`dispatch.py:727-729`).
1. The manager answers either escalation with `FIX` using the new `conflict` profile.
2. The worker rebases and the gate passes.
3. `manage.py:124` force-pushes before the review runs.
4. On `REVISE`, `manage.py:130` escalates but never removes the label.

Once CI on the new push is green and the branch is no longer behind main, `merge_pass_locked` merges code the reviewer rejected. Unreviewed code can also merge during the review window. This contradicts the README text added in this diff ("Only a fresh APPROVE restores `factory-approved`") and the plan's T6/§4.3 rule ("re-add `factory-approved` only on a fresh APPROVE"). The fix is to remove the label before the push in the `FIX` path.

**Plan drift.** No plan was recorded. Two changed files are not in the issue's "Touches" list: `README.md` (documentation for the new forms) and `tests/test_factory.py` (proof for the exit gate). Both are justified and show no defect. The issue required `uv run pytest tests/test_factory.py`; the gate report's `test: PASS` covers it.

**Abstractions.** `RESERVED_LABELS` is the only new one. It replaces a duplicated literal set that `ROUTE` parsing, `FIX` parsing and the prompt's label list now share, so it is justified.

```json
{"verdict":"REVISE","findings":[{"severity":"important","pass":"bug","path":"factory/manage.py","line":124,"summary":"FIX force-pushes new commits to agent/<n> before review without removing `factory-approved`, and the REVISE branch (manage.py:130) only escalates. The rebase-conflict and gate-after-rebase escalations (dispatch.py:722-729) leave that label on the PR, so a FIX with the `conflict` profile followed by REVISE leaves a labeled PR. The next merge pass merges reviewer-rejected code once CI is green, and unreviewed code can merge during the review window. This contradicts the README added in this diff (README.md:22, 'Only a fresh APPROVE restores factory-approved') and the plan's T6/§4.3 rule ('re-add factory-approved only on a fresh APPROVE'). Remove the label before the push."},{"severity":"nit","pass":"spec","path":"factory/dashboard.py","line":847,"summary":"`worker_when` is added to the snapshot, but dashboard.html never reads it; the config card (dashboard.html:894) shows only `cfg.workers`, so the dashboard never displays the when rules."},{"severity":"nit","pass":"bug","path":"factory/manage.py","line":110,"summary":"When the FIX preconditions fail (no worktree, PR not open, wrong branch), the ValueError ends as mechanism_failure with only a local log line. The issue gets no GitHub-visible diagnosis, even though the README says failure stays with the human."},{"severity":"nit","pass":"bug","path":"factory/config.py","line":291,"summary":"Unknown keys inside a `[workers.<label>]` table are silently ignored (KNOWN_KEYS['workers'] is None), so a misspelled `when` key drops the routing rule without any warning from `factory doctor`."}]}
```
VERDICT: REVISE

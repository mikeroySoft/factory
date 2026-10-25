**Standards** — No AGENTS.md/CONTRIBUTING.md in snapshot; README and `docs/manager-plan.md` are the conventions. Diff follows them (README updated, `RESERVED_LABELS` reuse, config validation style). One convention miss: the repo pushes rebased `agent/*` branches with `--force-with-lease` (`dispatch.py:731`, `refresh_pr_branch`); the new FIX path pushes plain.

**Spec** — `when` rules load (table-or-array, legacy arrays preserved), manager prompt lists labels with rules, ROUTE/FIX restricted to listed non-reserved labels, `ci-fix`/`conflict` profiles shipped in the template, dashboard exposes `worker_when`. Exit-gate scenarios are covered by tests; gate `test: PASS`. End-to-end trace: red CI → `merge_pass_locked` removes label + escalates (worktree kept until merge) → `manage_pass` holds the ticket lock → FIX runs `worker_round` with `{worker}` → gate → push → review → `approve_pr`. That path holds for `ci-fix`. It does not hold for `conflict`: the rebase-conflict escalation (`dispatch.py:722-724`) aborts the rebase, the shipped profile text instructs the worker to restart and complete the rebase (`factory.toml:47`), and the resulting non-fast-forward `git push origin agent/<n>` (`manage.py:124`) is rejected → `CalledProcessError` → `mechanism_failure`, worktree left diverged from the PR branch. The second shipped profile cannot complete the FIX flow it exists for.

**Plan drift** — No plan recorded. Beyond the issue's `Touches`: `README.md`, `tests/test_factory.py` (both expected by the exit gate / docs convention; not defects). Gate report covers the issue's only named proof (`pytest tests/test_factory.py`).

```json
{"verdict":"REVISE","findings":[
{"severity":"important","pass":"bug","path":"factory/manage.py","line":124,"summary":"FIX pushes with plain `git push origin agent/<n>`. The shipped `conflict` profile (templates/factory.toml:47) tells the worker to restart and finish the rebase after dispatch.py:722-724 aborted it, so the branch is rewritten and the push is non-fast-forward → CalledProcessError → mechanism_failure; the conflict profile can never re-gate/push/re-review. Repo precedent for rebased agent branches is `--force-with-lease` (dispatch.py:731)."},
{"severity":"nit","pass":"bug","path":"factory/manage.py","line":110,"summary":"FIX precondition failures (no worktree, PR not open, CHANGES_REQUESTED, wrong branch) raise ValueError, which manage_pass records as `github_command_failed` with no issue comment; the manage round is consumed silently and the human sees nothing on GitHub."}
]}
```

VERDICT: REVISE

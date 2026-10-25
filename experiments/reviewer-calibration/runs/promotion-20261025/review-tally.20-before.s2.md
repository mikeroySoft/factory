**Standards** — README updated with FIX/when semantics (review.diff:15-24); manager prompt/menu documented; config validation errors follow existing `ConfigError` `{path}: ...` pattern (review.diff:50-52). No AGENTS.md/CONTRIBUTING.md in repo; `docs/manager-plan.md` T11 scope matches. Ruff `line-length = 100` is configured but E501 is not enabled and pre-existing `manage.py` lines already exceed it — not a rule.

**Spec (issue #20)** — traced:
- `[workers.<label>]` table-or-array with `when`: config.py review.diff:44-55; legacy arrays still load (test review.diff:236-254). ✓
- Prompt lists labels with `when`; ROUTE/FIX reject unlisted labels: review.diff:120-126, 176-178, 187. ✓
- `ROUTE chore` without a chore worker → HUMAN recorded, no `issue edit`; with one → `--add-label chore` (test review.diff:263-286). ✓
- `FIX` on red CI runs selected argv in kept worktree, re-gates, pushes only on PASS, re-reviews, approves only on fresh APPROVE (review.diff:139-166; test review.diff:288-330). ✓
- `ci-fix`/`conflict` profiles shipped as commented host-config templates (review.diff:218-224); consistent with "human-applied". ✓
- Gate `test: PASS` covers the pytest requirement.

**Defect found tracing the `conflict` profile end-to-end**: `refresh_pr_branch` (snapshot dispatch.py:722-724) aborts a conflicting rebase and escalates. A manager `FIX {"worker":"conflict"}` then runs the shipped profile, which rebases `agent/<n>` onto main (rewritten history). The FIX path pushes with plain `git push origin agent/<n>` (review.diff:159), which the remote rejects as non-fast-forward → `CalledProcessError` → `mechanism_failure`, "leaving for human" (snapshot manage.py:222-226). The existing rebase path uses `--force-with-lease` (dispatch.py:731). As shipped, the `conflict` profile can never complete through FIX; the only consumer of that profile in this diff is broken for its sole purpose. Fix: `--force-with-lease` on the FIX push (lease still protects a concurrent human push; branch identity is already verified at review.diff:146).

**Plan drift** — No plan recorded. Files beyond issue "Touches": `README.md` (docs for the new decision), `tests/test_factory.py` (exit-gate tests). Both justified; no drift defect. Gate report lacks a named `pytest tests/test_factory.py` line but `test: PASS` is the configured check.

```json
{"verdict":"REVISE","findings":[{"severity":"important","pass":"bug","path":"factory/manage.py","line":159,"summary":"FIX pushes with plain `git push origin agent/<n>`; the shipped `conflict` profile (templates/factory.toml diff lines 222-224) rewrites history via rebase, so the push is rejected non-fast-forward → CalledProcessError → mechanism_failure every time. refresh_pr_branch already uses `--force-with-lease` for the same case; FIX must too."},{"severity":"nit","pass":"bug","path":"factory/manage.py","line":162,"summary":"A FIX whose worker makes no commits (ci-fix declares a flake) re-APPROVEs the same still-red head; merge stage removes factory-approved and re-escalates. Bounded by manager_rounds, but churns a round with no state change; consider skipping approve when HEAD is unchanged or noting the limitation."}]}
```

VERDICT: REVISE

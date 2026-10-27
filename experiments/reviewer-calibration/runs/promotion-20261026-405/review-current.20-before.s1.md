**Spec.** Issue #20 scope and exit gate are covered: table-or-array `[workers]` (config.py:285-298, test at test_factory.py:147), `when` listed in the manager prompt and ROUTE/FIX restricted to listed labels (manage.py:199-213), `ci-fix`/`conflict` profiles in the template (factory.toml:41-47), FIX on red CI runs the selected argv (manage.py:117-120, test at test_factory.py:288). README documents the new decision and config form.

**Standards.** No AGENTS.md/CONTRIBUTING.md in snapshot; README and `factory init` template (the documented config reference per SKILL.md:23-24) both updated.

**Plan drift.** Plan not recorded. Beyond the issue's Touches list, the diff changes `README.md` and `tests/test_factory.py` — both required (docs, exit-gate tests). Gate `test: PASS` covers the stated `uv run pytest tests/test_factory.py` proof.

**Defect.** The diff ships a `conflict` profile whose instructions rewrite branch history (factory.toml:224: "If the rebase was aborted, restart it against the configured main"), which is exactly the escalation produced at dispatch.py:722-724. But the FIX path pushes with a plain `git push origin agent/<n>` (manage.py:124). A rebased branch is non-fast-forward, so the push is rejected after the gate has already passed → `CalledProcessError` → `mechanism_failure` (manage.py:222-226); no review runs, the worktree is left diverged from the remote, and the README claim "re-gates, pushes only on gate PASS, and re-reviews" is false for the conflict profile. The repo's own rebase path uses `--force-with-lease` (dispatch.py:731); FIX should do the same.

```json
{"verdict":"REVISE","findings":[
{"severity":"important","pass":"bug","path":"factory/manage.py","line":124,"summary":"FIX pushes with plain `git push origin agent/<n>`; the shipped `conflict` profile (templates/factory.toml:224) tells the worker to restart the rebase onto main, so the branch is non-fast-forward and the push is rejected after a passing gate → CalledProcessError → mechanism_failure, no review, worktree diverged from remote. Use `--force-with-lease` as dispatch.refresh_pr_branch does (dispatch.py:731)."},
{"severity":"nit","pass":"bug","path":"factory/manage.py","line":109,"summary":"`pr[\"headRefName\"] != f\"agent/{n}\"` is tautological: the PR was looked up by that branch name (`gh pr view agent/<n>`); dead check."},
{"severity":"nit","pass":"spec","path":"factory/dashboard.py","line":847,"summary":"`worker_when` is added to the snapshot but dashboard.html's workers card (dashboard.html:893-894) does not render it; the data is exposed but invisible."},
{"severity":"nit","pass":"spec","path":"factory/config.py","line":291,"summary":"Unknown keys inside a `[workers.<label>]` table (e.g. a misspelled `when`) are silently ignored; `KNOWN_KEYS[\"workers\"] is None` so `factory doctor` reports no drift for them."}
]}
```

VERDICT: REVISE

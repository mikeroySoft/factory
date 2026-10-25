```json
{"verdict":"APPROVE","findings":[{"severity":"nit","pass":"bug","path":"factory/config.py","line":48,"summary":"Table-form workers deep-merge across host/repo layers (config.merge recurses on dicts): a repo `[workers.chore] command=[...]` over a host `[defaults.workers.chore] {command, when}` silently inherits the host `when` rule into the manager prompt. Not required by #20; document or flatten only if host profiles are expected to be overridden per repo."}]}
```

Trace notes (no Important findings):
- Spec: `when` on `[workers.<label>]` tables with legacy arrays preserved (config.py:45-55, test diff:236-254); manager prompt lists labels with `when` and reserved labels filtered (manage.py diff:176-178); ROUTE/FIX accept only listed labels (manage.py diff:112, 120-123; tests diff:263-286, 349-359); `ci-fix`/`conflict` profiles shipped as commented host-config profiles matching the template's all-commented convention (factory.toml diff:218-224); FIX on red CI runs the selected argv in the kept worktree, re-gates, pushes only on PASS, re-reviews, re-labels `factory-approved` only on APPROVE (manage.py diff:139-166; test diff:288-347). Worker selection via `cfg.worker({label})` first-match resolves the validated label correctly; `pr view` guard refuses CHANGES_REQUESTED and non-OPEN PRs; force-with-lease fails closed on a diverged remote. Out of scope respected: no host-config writes.
- Standards: no AGENTS.md/CONTRIBUTING.md; README and plan T11 updated consistently. CI runs only unittest; ruff line-length not enforced and already exceeded in base code, so not reported.
- Plan drift: no plan recorded. Files beyond issue Touches: README.md (docs for the new decision) and tests/test_factory.py (the issue's own verification command). Gate report covers test PASS; nothing planned is missing.

VERDICT: APPROVE

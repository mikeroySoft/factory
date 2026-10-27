**Standards.** No AGENTS.md/CONTRIBUTING.md in the snapshot; only `pyproject.toml` (`[tool.ruff] line-length = 100`, not run by the gate or CI) and README/docs. New code matches existing `manage.py`/`config.py` patterns (`dispatch.*` helpers reused, `ConfigError` paths, `record` before mutation). Two new lines exceed the configured ruff width (`factory/config.py:49`, `factory/manage.py:144` in diff numbering); pre-existing lines in the same file already do, so nit only.

**Spec (#20 + brief).**
- `when` on `[workers.<label>]`, array-or-table loading, validation: `factory/config.py:45-55`; legacy array coverage at `tests/test_factory.py:236-254`. ✔
- Prompt lists labels with `when`: `factory/manage.py:176-178`. ✔
- ROUTE/FIX only for listed labels: `factory/manage.py:112,120-123`, `187` passes filtered workers; tests `263-286`, `332-342`. ✔ (exit gate "ROUTE chore rejected and recorded / applied" covered.)
- FIX on red CI runs the selected argv, re-gates, pushes only on PASS, re-reviews, re-approves only on fresh APPROVE: `factory/manage.py:139-166`; test `288-330`. ✔ Matches plan T6 wording ("one `worker_round` in the kept worktree with the guidance as `extra`").
- Profiles in `templates/factory.toml:218-224`, commented out. Defensible under "remain host-config, human-applied", but note a fresh `factory init` has no `ci-fix` label, so FIX `ci-fix` is rejected until a human copies the block. Nit, not a criterion failure.
- Out of scope respected: nothing writes host config.

**Plan drift.** Touches named `config.py`, `manage.py`, `dashboard.py`, `templates/factory.toml`. Also changed: `README.md` (docs for the new decision/table form) and `tests/test_factory.py` (required by the exit gate's `uv run pytest tests/test_factory.py`). Neither exposes a defect. Gate report shows `test: PASS` without naming the command; no planned proof otherwise missing.

**Other observations (non-blocking).** `factory/manage.py:144-147`: FIX precondition failures raise `ValueError` after the `manage` event is recorded (`manage.py:193`), so the round is consumed and the ticket gets no issue comment; `manage.py:199` tags it `github_command_failed`. Human sees only the dispatcher log. Same shape as existing partial-SPLIT handling, so nit.

```json
{"verdict":"APPROVE","findings":[
{"severity":"nit","pass":"spec","path":"factory/config.py","line":49,"summary":"Line exceeds pyproject `[tool.ruff] line-length = 100` (also factory/manage.py:144); not gate-enforced and pre-existing elsewhere, so informational."},
{"severity":"nit","pass":"spec","path":"factory/templates/factory.toml","line":218,"summary":"`ci-fix`/`conflict` profiles are shipped commented out; a fresh `factory init` repo rejects `FIX ci-fix` as unlisted until a human copies them into host config. Consistent with 'human-applied' but worth stating explicitly in the template comment that FIX needs the label configured."},
{"severity":"nit","pass":"bug","path":"factory/manage.py","line":144,"summary":"FIX precondition failures (no worktree / PR not open / CHANGES_REQUESTED / wrong branch) raise after the `manage` round is recorded, so the round is consumed with no issue comment and outcome reason `github_command_failed`; the human only learns why from the dispatcher log."}
]}
```
VERDICT: APPROVE

**Diff source**: `./review.diff` absent in this workspace; used `git diff origin/main...HEAD` (byte-identical to the sibling workspace's `derived.diff`, 6 files, +192/−8).

## Standards
- No AGENTS.md/CONTRIBUTING.md in repo; `docs/manager-plan.md` T11 (lines 341–352) and README are the documented sources. Diff follows them: `[workers]` table-or-array (`factory/config.py:288-298`), prompt lists labels with `when` (`factory/manage.py:199-201`), ROUTE/FIX restricted to listed labels (`manage.py:54-67`).
- Template convention "defaults are shown commented out" (`factory.toml:2`) → commented `ci-fix`/`conflict` profiles (`factory.toml:41-47`) are consistent.
- `pyproject.toml` `line-length = 100` is formatter-only (no ruff in CI/gate, E501 not default); baseline already has 254 over-length lines. Not reported.
- Smells: none material. `RESERVED_LABELS` extraction (`manage.py:32`) reuses an existing set; no new abstractions beyond the brief.

## Spec (issue #20 + agent brief)
- `when` on `[workers.<label>]`, legacy array still loads: `config.py:290-298`, test `tests/test_factory.py:147-165`. ✓
- Prompt lists labels with `when`: `manage.py:200-201`, asserted at `test_factory.py:673`. ✓
- ROUTE `chore` without worker → HUMAN and recorded; with worker → label applied: `test_factory.py:652-676`. ✓
- FIX on red CI runs the `ci-fix` argv in kept worktree, re-gates, pushes only on PASS, re-reviews, `factory-approved` only on fresh APPROVE: `manage.py:104-131`, `test_factory.py:678-719`. ✓ Matches plan T10 line 283-284.
- Unlisted/reserved FIX worker rejected: `manage.py:62-64`, `test_factory.py:721-731`. ✓
- Out of scope respected: no host-config writes.
- Consumers of `cfg.workers` (`onboard.py:223`, `stats.py:71`, `dashboard.py:698,846`) still receive `dict[label, argv]`; unchanged contract.

## Plan drift
Plan not recorded; against the issue's Touches list, unnamed files: `README.md` (documents the new FIX decision/`when` rules — required since README is the manager-menu doc) and `tests/test_factory.py` (exit gate requires the suite to pass). Planned proof: `uv run pytest tests/test_factory.py` — gate `test: PASS` covers it. No missing proof; no drift exposing a defect.

```json
{"verdict":"APPROVE","findings":[{"severity":"nit","pass":"spec","path":"factory/templates/factory.toml","line":41,"summary":"Issue says \"ship two default profiles\"; they ship as commented-out examples with copy-to-host instructions (lines 37-39). Consistent with the template's 'defaults shown commented out' convention and the 'human-applied' clause, but confirm an active-by-default profile was not intended."},{"severity":"nit","pass":"bug","path":"tests/test_factory.py","line":678,"summary":"`import shutil` inside the test method; move to module imports like the rest of the file."}]}
```

VERDICT: APPROVE

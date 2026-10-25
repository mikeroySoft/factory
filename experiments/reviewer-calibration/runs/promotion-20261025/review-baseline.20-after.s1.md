**Note on inputs:** `./review.diff` and `./snapshot` do not exist in the sandbox. Reviewed `git diff 07913b4...ce5568e` (merge-base with `origin/main` → gated head) in the checkout at `repo/`; line numbers below are from the gated revision's files. Issue #20 and its triage comment fetched from GitHub.

## Standards

No `AGENTS.md`/`CONTRIBUTING.md` in repo; documented conventions are README, `docs/manager-plan.md`, `pyproject.toml`, CI (`python -m unittest discover -s tests`).

- Tests follow existing `unittest` + stub-binary pattern in `tests/test_factory.py:147-165, 652-749`. Consistent.
- Template keeps every key commented, matching the file's "defaults are shown commented out" convention (`factory/templates/factory.toml:31-47`).
- `[tool.ruff] line-length = 100` (`pyproject.toml:34`): `factory/config.py:292` (130 cols), `factory/manage.py:109` (136), `:110` (109) exceed it. Not CI-enforced and `manage.py` already has 26 such lines → optional only.
- Manager-plan §4.3 authority boundary ("Change `[workers]` host config: human/District applies", `docs/manager-plan.md:153`) respected: `parse` rejects unlisted labels (`factory/manage.py:54,63-66`), no config writes.

## Spec (issue #20 + triage brief)

| Criterion | Status | Evidence |
|---|---|---|
| `[workers.<label>]` may carry `when`; array form still loads | Met | `factory/config.py:288-298`; test `tests/test_factory.py:147-165` |
| Manager prompt lists labels with `when` | Met | `factory/manage.py:199-201` |
| ROUTE/FIX only for listed labels | Met | `factory/manage.py:54, 63-66, 213` |
| `ci-fix` and `conflict` profiles in template | Met (commented, human-applied) | `factory/templates/factory.toml:41-47` |
| ROUTE chore rejected+recorded w/o chore worker; label applied with one | Met | `tests/test_factory.py:652-676` |
| FIX on red CI runs selected argv | Met | `factory/manage.py:117-120`; test `:678-736` |
| `dashboard.py` touched | Met (snapshot field only) | `factory/dashboard.py:847` |
| Out of scope: manager writing host config | Respected | — |

### Required fixes

1. **Stale `factory-approved` survives a FIX that the reviewer rejects — merge-gate bypass.** `factory/manage.py:125` force-pushes worker-modified commits, then on REVISE only escalates (`:131`); the label is never removed. Trigger: rebase-conflict escalation (`factory/dispatch.py:722-724`) happens on an already-approved PR and does not strip the label (only the CI-fail path at `dispatch.py:825-837` does). Manager issues `FIX conflict` → worker edits → gate PASS → push → reviewer REVISE → PR still carries `factory-approved` → merge stage (`dispatch.py:803`) merges the rejected head once CI is green. Contradicts the diff's own documented contract: "Only a fresh APPROVE restores `factory-approved`; failure stays with the human" (`README.md:98-99`) and plan rule "Re-add `factory-approved` … after gate PASS + fresh codex APPROVE" (`docs/manager-plan.md:143`). Fix: remove `FACTORY_APPROVED` from the PR before the push at `manage.py:125` (or on the non-APPROVE branch at `:130-131`); `approve_pr` re-adds it on APPROVE. One `gh pr edit --remove-label` call; no new abstraction.

### Optional suggestions

- `unknown_keys` skips `workers` entirely (`factory/config.py:58` `"workers": None`), so `[workers.ci-fix] wehn = "..."` is silently dropped by `config.py:291` and `factory doctor` won't flag it. Consider checking `{"command","when"}` per table entry.
- `worker_when` is exported in the dashboard snapshot (`factory/dashboard.py:847`) but nothing renders it; issue only says "touches dashboard.py", so acceptable as-is.
- Line-length overruns listed under Standards.

## Plan drift

Plan status: not recorded → no plan to diff against. Against the issue's "Touches" list (`config.py`, `manage.py`, `dashboard.py`, `templates/factory.toml`), the diff additionally changes `README.md` (docs for new FIX decision — appropriate) and `tests/test_factory.py` (the issue's named verification file). No unexplained files. Gate report shows `test: PASS` but does not name `uv run pytest tests/test_factory.py` (the issue's stated command); CI runs `unittest discover`, which includes that file, so treated as covered. Drift exposes no defect.

VERDICT: REVISE

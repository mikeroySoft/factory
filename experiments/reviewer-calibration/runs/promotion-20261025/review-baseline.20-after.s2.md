## Inputs

`./review.diff` and `./snapshot` were absent from the sandbox; reviewed `git diff 07913b4..ce5568e` (commits 7fc5fe2, 8dd6588, ce5568e) in the checked-out gated revision instead. Line refs below are to files at ce5568e.

## Standards

No `AGENTS.md`/`CONTRIBUTING.md` in the repo; only documented conventions are `pyproject.toml:33-35` (ruff `line-length = 100`, default rule set, so E501 is not enforced) and the design doc `docs/manager-plan.md`.

- `docs/manager-plan.md:341-352` (T11) and `:153` ("Change `[workers]` host config → recommend in diagnosis; human applies"): respected. The profiles ship commented-out in `factory/templates/factory.toml:41-47`, matching the template's "defaults shown commented out" convention (`:2-3`); the manager path never writes config.
- Smell baseline, judgement calls only:
  - `factory/manage.py:54` still subtracts `RESERVED_LABELS` although `manage_pass` already filters (`:199`, `:213`). Harmless redundancy; `parse` is public so the defensive copy is defensible.
  - `factory/manage.py:109` `pr["headRefName"] != f"agent/{n}"` is tautological — the PR was looked up by that branch (`:107`). Dead guard.
  - `factory/config.py:292` is ~150 chars against the configured 100; not enforced and neighbouring lines (`:274`) already exceed it.

## Spec (issue #20 + agent brief)

| Criterion | Evidence | Status |
|---|---|---|
| `[workers.<label>]` may carry `when`; array form still loads | `factory/config.py:288-298`; test `tests/test_factory.py:147-165` | ✓ |
| Manager prompt lists labels with their `when` | `factory/manage.py:199-201` (`- label: when` / `(no when rule)`) | ✓ |
| ROUTE only accepted for listed labels; `ROUTE chore` without `chore` worker rejected and recorded; with one, label applied | `factory/manage.py:53-57`, `:72`, `:219`; test `:652-675` asserts HUMAN decision recorded / `--add-label chore` | ✓ |
| FIX only accepted for listed labels | `factory/manage.py:63-69`; test `:745-755` (rejects `ci-fix` unconfigured, `default`, reserved, non-string) | ✓ |
| FIX on red CI runs the `ci-fix` argv | `factory/manage.py:104-131` → `dispatch.worker_round(n, wt, {data["worker"]}, …)` → `cfg.worker` first-match (`factory/config.py:135-138`); test `:677-743` asserts worker ran, guidance + packet delivered, push only on gate PASS, `factory-approved` only on APPROVE | ✓ |
| Two default profiles in `templates/factory.toml` | `:41-47`; `expand` is substring replace (`factory/config.py:147-154`) so `{prompt}`/`{cwd}` inside the instruction string resolve | ✓ |
| `dashboard.py` touched | `factory/dashboard.py:847` exposes `worker_when` in snapshot config | ✓ |
| Out of scope: manager writing host config | no config writes in diff | ✓ |

Correctness checks on the FIX path (all consistent with existing callers): `worker_round`/`review`/`pr_comment`/`approve_pr`/`escalate` signatures match `factory/dispatch.py:915-923, 409, 481, 673, 375`; `--force-with-lease` (`manage.py:124`) mirrors `refresh_pr_branch` (`dispatch.py:731`) and is required for the `conflict` profile's rebase; a failed FIX re-escalates at round+1 (`dispatch.py:369-371`), which `manage_pass` refuses beyond `manager_rounds` (`manage.py:189`), so FIX rounds are bounded.

## Plan drift

Plan not recorded. Relative to the issue's `Touches` list, the diff additionally changes `README.md` (documents the new `FIX` decision and table-form workers, `README.md:88-102`) and `tests/test_factory.py` (exit-gate tests). Both directly serve the acceptance criteria; no defect exposed. Planned proof `uv run pytest tests/test_factory.py` is covered by the gate's `test: PASS` (`.factory.toml:4-14` runs `unittest discover -s tests`, which includes that file).

## Required fixes

None.

## Optional suggestions

- `factory/manage.py:109`: drop the `headRefName` comparison (always true after lookup by branch).
- `factory/manage.py:54`: `set(workers) - RESERVED_LABELS` is now redundant with the pre-filtered `workers` from `:199`; keep only if `parse` is meant to stay safe for raw `cfg.workers` callers.
- `factory/config.py:292`: wrap to ≤100 cols per `pyproject.toml:34` if the repo ever enables E501.

VERDICT: APPROVE

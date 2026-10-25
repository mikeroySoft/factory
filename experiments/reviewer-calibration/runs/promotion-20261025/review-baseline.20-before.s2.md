## Standards

No `AGENTS.md`/`CONTRIBUTING.md` in the snapshot; `README.md` and `docs/manager-plan.md` are the documented conventions.

- README updated for the new decision and config form (`review.diff:10-24`); template documents the table form and profiles (`review.diff:208-224`). Consistent with README's "every key documented" convention.
- FIX path (`review.diff:139-166`) mirrors the existing review-bounce sequence in `dispatch.py:1056-1070` (worker_round → gate → push → review → pr_comment → approve_pr/escalate) rather than inventing a second mechanism. No new abstractions beyond `RESERVED_LABELS` (`review.diff:102`), which replaces an inline set literal already present at `review.diff:111`.
- `manage` event recorded before mutation (`review.diff:193`), matching README's "records a `manage` event before GitHub mutations".

No standards violations found.

## Spec (issue #20 / plan T11)

| Criterion | Evidence | Status |
|---|---|---|
| `[workers.<label>]` may carry `when` | `review.diff:36,45-55` | ✓ |
| Manager prompt lists labels with `when` | `review.diff:176-178` | ✓ |
| ROUTE/FIX only for listed labels | `review.diff:112-115`, `review.diff:120-123`, `review.diff:186-187` | ✓ |
| Ship `ci-fix` and `conflict` profiles in template | `review.diff:218-224` | ✓ (commented, matching the template's all-defaults-commented style; brief says "remain host-config that humans apply") |
| Array-form `[workers]` still loads | `review.diff:47`, test `review.diff:240,298` | ✓ |
| Exit gate: ROUTE chore rejected+recorded without worker, applied with | test `review.diff:263-286` | ✓ |
| Exit gate: FIX on red CI runs `ci-fix` argv | test `review.diff:288-330`; `cfg.worker({data["worker"]}, …)` at `review.diff:153` selects by exact label | ✓ |
| Out of scope: manager writing host config | no config writes in diff | ✓ |
| Dashboard touch | `review.diff:67` | ✓ (exposed in JSON; not rendered in `dashboard.html`, not required) |

Safety properties claimed in README hold in code: push only after gate PASS (`review.diff:156-159`), `factory-approved` only on fresh APPROVE (`review.diff:162-163`), failure → `escalate` (`review.diff:157,165`), no requeue (early `return` at `review.diff:166` skips the `ready-for-agent` relabel at `manage.py:158-159`). Merge stage keys on the PR label only (`dispatch.py:803`), so a FIX-approved PR merges next pass.

## Plan drift

Plan status: not recorded. Against the issue's "Touches" list (config.py, manage.py, dashboard.py, factory.toml), extra files are `README.md` and `tests/test_factory.py` — both expected (doc convention; exit-gate tests). Gate `test: PASS` runs `python -m unittest discover -s tests` (`snapshot/.factory.toml:4-13`), which includes `tests/test_factory.py`; equivalent to the issue's `uv run pytest tests/test_factory.py` [INFERENCE: pytest collects the same unittest classes]. No planned proof missing.

## Required fixes

None.

## Optional suggestions

- `review.diff:144`: `pr["headRefName"] != f"agent/{n}"` is tautological — `gh pr view agent/{n}` already selects by branch. Harmless; could drop.
- `review.diff:112`: `set(workers) - RESERVED_LABELS` in ROUTE is now redundant since `workers` is pre-filtered at `review.diff:176`. Harmless.
- `review.diff:47-48`: unknown keys in a `[workers.<label>]` table (e.g. `wehn`) are silently ignored; `KNOWN_KEYS["workers"] = None` (`config.py:58`) means `factory doctor` won't report them as drift. Consider a check if typos bite in practice.
- `review.diff:166`: on FIX + APPROVE nothing is posted to the *issue* (only `pr_comment` on the PR); the human watching `ready-for-human` sees no trace until merge. A one-line `gh issue comment` would close that gap; documented behavior, so not blocking.

VERDICT: APPROVE

## Standards

Snapshot has no `AGENTS.md`/`CONTRIBUTING.md`; documented sources are `README.md`, `docs/manager-plan.md`, `pyproject.toml` (`[tool.ruff] line-length = 100`).

- No documented-rule violations. README manager section updated in step with the new decision (`review.diff:10-24`); `unknown_keys` already skips `workers` sub-keys (`snapshot/factory/config.py:218-220`), so `when` won't surface as doctor drift.
- Judgement calls (non-blocking):
  - `review.diff:49` (`config.py`) and `:144` (`manage.py`) exceed 100 cols. Ruff default rule set does not select E501 and pre-existing lines already exceed it (`snapshot/factory/manage.py:211`), so not enforced — skip or wrap.
  - `review.diff:122` re-checks `worker in RESERVED_LABELS` though `parse` now receives the already-filtered dict (`review.diff:176,187`). Harmless belt-and-braces.
  - Possible Long Function: FIX branch (`review.diff:139-166`) lives inside `apply` but uses none of its `gh` closure. Fine at this size.

## Spec (issue #20 + triage brief)

Acceptance criteria vs diff:

| Criterion | Evidence | Status |
|---|---|---|
| `[workers.<label>]` may carry `when` | `review.diff:45-55` table-or-array parse; `worker_when` field `:36` | met |
| Manager prompt lists labels with their `when` | `review.diff:176-178` | met |
| ROUTE/FIX accepted only for listed labels | `review.diff:112-115`, `:120-123`; filtered dict passed `:187` | met |
| Ship `ci-fix` and `conflict` profiles in template | `review.diff:218-224` (commented, human-applied) | met |
| Profiles stay host-config, human-applied; manager can't write config | `review.diff:213-216`, README `:23-24`; no code path writes config | met |
| Exit: ROUTE chore rejected+recorded without worker, applied with one | `review.diff:263-286` | met |
| Exit: FIX on red CI runs `ci-fix` argv | `review.diff:288-347` (`fix-worker` stub, `pr checks` fail) | met |
| Exit: array-form `[workers]` still loads | `review.diff:236-248` | met |
| `uv run pytest tests/test_factory.py` passes | gate `test: PASS` | met |

Correctness checks on the FIX path (not in issue text but concrete):
- Runs under the ticket flock held by `manage_pass` (`snapshot/factory/manage.py:174-182`), so a concurrent dispatch pass can't enter `wt-<n>`. OK.
- Push only on gate PASS (`review.diff:156-159`); `--force-with-lease` needed for the rebase profile; `factory-approved` only on fresh APPROVE (`:162-163`), matching plan §4.3 row "after gate PASS + fresh codex APPROVE" (`docs/manager-plan.md:143`). Merge stage keys on the PR label only (`snapshot/factory/dispatch.py:803`), so leaving the issue `ready-for-human` doesn't block merge. OK.
- FIX failure re-escalates (`:157,:165`); re-entry is bounded by `manager_rounds` (`snapshot/factory/manage.py:189`). No loop.

Scope creep: none. Precondition `reviewDecision != CHANGES_REQUESTED` (`review.diff:144`) is a sensible guard not in the issue; README only says "open PR" (`:19`) — optional: mention it in README since the ValueError message does.

## Plan drift

Plan status: not recorded — no plan to diff against. Against issue "Touches" (config.py, manage.py, dashboard.py, factory.toml): extra files `README.md` and `tests/test_factory.py`. Both required by the brief (exit gate is test-defined; README is the documented manager contract). Planned proof: only the issue's `uv run pytest tests/test_factory.py` → gate `test: PASS`. No missing proof.

## Required fixes
None.

## Optional suggestions
- Document the `CHANGES_REQUESTED` refusal in README (`review.diff:18-20`).
- Wrap `review.diff:49` / `:144` to ≤100 cols per `pyproject.toml` ruff setting.
- Drop the redundant `RESERVED_LABELS` check at `review.diff:122` (callers already filter), or keep as defense — either is fine.

VERDICT: APPROVE

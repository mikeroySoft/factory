## Standards

Sources checked: `AGENTS.md`/`CLAUDE.md` (none in repo; host-level only), no `CONTRIBUTING.md`, `README.md`, `docs/manager-plan.md` (T6/T11), `pyproject.toml` (`[tool.ruff] line-length = 100`), `.github/workflows/ci.yml` (unittest only; ruff not run).

No hard violations.

Judgement calls (baseline smells):
- **Long lines** — `factory/config.py:292` (130 cols), `factory/manage.py:109-110` (136/109), plus ~15 test lines exceed the `line-length = 100` in `pyproject.toml:34`. Pre-existing code already does the same (`manage.py:102`, `manage.py:211`, `config.py:274`) and nothing enforces it (ruff absent from gate/CI, E501 not a ruff default). Optional only.
- **Speculative Generality / dead data** — `factory/dashboard.py:847` adds `worker_when` to the snapshot but `dashboard.html` never reads it (`dashboard.html:894` renders `cfg.workers` only). Issue lists `dashboard.py` under Touches, so the edit is in-brief; rendering it is optional.
- `RESERVED_LABELS` (`manage.py:33`) consolidates a set previously inlined once and now used three times — not a net-new abstraction, fine.

## Spec

Issue #20 + T11 brief, point by point:

| Requirement | Evidence | Status |
|---|---|---|
| `[workers.<label>]` may carry `when` | `factory/config.py:289-298` | ✓ |
| Array form still loads | `config.py:290-291` (`entry` non-dict path); `tests/test_factory.py:147-158` | ✓ |
| Manager prompt lists labels with `when` | `factory/manage.py:199-201` | ✓ |
| ROUTE only for listed labels | `manage.py:54` (`set(workers) - RESERVED_LABELS`), `manage.py:213` passes the same filtered dict used to build the prompt | ✓ |
| FIX only for listed labels | `manage.py:63-69` | ✓ |
| FIX on red CI runs the selected argv | `manage.py:117-120` → `dispatch.worker_round` → `cfg.worker({worker},…)` (`config.py:137`, first-match on the single label) | ✓ |
| Ship `ci-fix` and `conflict` profiles in template; remain human-applied host config | `factory/templates/factory.toml:41-47`, commented like every other worker example in that file (`:26-30`) | ✓ |
| Out of scope: manager writing host config | no config writes in diff | ✓ |
| Exit gate: ROUTE chore rejected+recorded without worker, applied with | `tests/test_factory.py:652-676` | ✓ |
| Exit gate: FIX runs ci-fix argv | `tests/test_factory.py:678-737` | ✓ |
| `uv run pytest` passes | gate report | ✓ |
| README documents FIX + table form | `README.md:88-102` | ✓ |

Scope: FIX dispatch itself is T6 in the plan, but the #20 brief's exit gate ("a FIX on a red CI run executes the ci-fix argv") requires it and `origin/main` has no FIX, so implementing it is in-brief, not creep. Preconditions at `manage.py:109-112` (open PR, kept worktree, branch checked out, no human CHANGES_REQUESTED) match the plan's "kept worktree / open PR" wording and mirror `dispatch.py:805`.

Correctness traced:
- Red-CI escalation keeps the worktree (`dispatch.py:838-843`; cleanup only in `cleanup_after_merge`), and `refresh_pr_branch` aborts a conflicted rebase (`dispatch.py:723`) so `git branch --show-current` at `manage.py:111` returns `agent/<n>` for both `ci-fix` and `conflict` cases.
- Push only on gate PASS (`manage.py:121-124`), `--force-with-lease` against the un-fetched tracking ref rejects a human push made during the worker run → `CalledProcessError` → `mechanism_failure` (`manage.py:222-226`). Fine.
- Gate-fail / REVISE paths call `escalate()` (`manage.py:122,130`) which bumps the round (`dispatch.py:369-371`); `manage_pass:189` bounds re-entry by `manager_rounds`. Bounded.
- Stricter loader: a string `default = "omp -p"` previously char-split via `list(v)`; now `ConfigError` (`config.py:292`). Template examples are all arrays — no regression.

## Changed signatures / defaults — consumers outside the diff

- `apply(n, issue, decision, body, data, packet)` — sole caller `manage.py:221` updated. Holds.
- `parse(output, workers)` now receives the reserved-filtered dict — sole caller `manage.py:213`. Holds.
- `Config.workers` shape unchanged (label→argv): `config.py:137`, `dashboard.py:698`, `dashboard.py:846`, `dashboard.py:873` (`stats.worker_metrics`), `onboard.py:223-224`, `stats.py:316`. All hold.
- `Config.worker_when` new, default `{}` — read only at `manage.py:201`, `dashboard.py:847`. Holds.
- Snapshot key `worker_when` — additive; `dashboard.html` ignores it. Holds.
- `MENU` adds FIX — consumer is the manager model; `parse` accepts it. Holds.

## Required fixes

None.

## Optional suggestions

1. `factory/config.py:292`, `factory/manage.py:109-110` — wrap to ≤100 cols per `pyproject.toml:34`; non-enforced, matches surrounding code either way.
2. `factory/config.py:289-298` — `KNOWN_KEYS["workers"] is None` (`config.py:58,219`) means `factory doctor` won't flag a typo like `wehn = "..."` inside a worker table; the rule silently becomes "(no when rule)". Reporting keys other than `command`/`when` in table-form entries would catch it. Not an acceptance criterion.
3. `factory/dashboard.py:847` — either render `worker_when` in `dashboard.html:894` or drop the snapshot key; currently unused.

VERDICT: APPROVE

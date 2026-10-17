## Standards

Sources: `README.md`, `docs/manager-plan.md` (§4.3, T6, T11), `factory/templates/factory.toml` header convention ("defaults are shown commented out"), `pyproject.toml` (`[tool.ruff] line-length = 100`, not enforced by the gate — `.factory.toml` runs only `unittest`; main already has hundreds of >100-col lines, so not reported).

- Template profiles as commented blocks (`factory/templates/factory.toml:41-47`) match the file's documented convention and the issue's "remain host-config, human-applied".
- `RESERVED_LABELS` (`factory/manage.py:32`) replaces an inline set that was already used once and is now needed three times — dedup, not a new abstraction.
- `Config.worker_when` as a sibling dict (`factory/config.py:103`) keeps `Config.workers: dict[str, list[str]]` unchanged, so no consumer rewrite. Judgement call (possible Data Clump: label → {argv, when}); justified here by the zero-churn on six consumers below.
- Smell baseline: `import shutil` inside a test body (`tests/test_factory.py:678`) — judgement call, not a documented rule.

No documented-standard violations found.

## Spec (issue #20 + agent brief)

| Criterion | Status | Evidence |
|---|---|---|
| `[workers.<label>]` may carry `when`; table-or-array | Met | `factory/config.py:289-298` |
| Manager prompt lists labels with their `when` | Met | `factory/manage.py:199-201` |
| ROUTE/FIX only for listed labels | Met | `factory/manage.py:53-57`, `63-66`; parse now receives the reserved-filtered dict (`:213`) |
| `ci-fix` / `conflict` profiles in template | Met | `factory/templates/factory.toml:41-47` |
| Array-form `[workers]` still loads | Met | `tests/test_factory.py:147-158` |
| ROUTE chore w/o worker rejected + recorded; with worker label applied | Met | `tests/test_factory.py:652-676` |
| FIX on red CI runs `ci-fix` argv | Met | `tests/test_factory.py:678-724` |
| Out of scope: manager writing host config | Respected | no config writes in diff |

Scope beyond the brief: the FIX execution path itself (`factory/manage.py:104-131`) is plan T6 work, but the issue's exit gate ("a FIX on red CI runs the ci-fix argv") requires it, so it is in scope.

## Changed signatures / defaults — consumers outside the diff

- `apply(n, issue, decision, body, data, packet)` — sole caller `factory/manage.py:221` updated. Holds.
- `parse(output, workers)` — sole caller `factory/manage.py:213`; now gets the reserved-filtered dict; ROUTE validation unchanged. Holds.
- `Config.workers` type unchanged (`dict[str, list[str]]`, normalized at load): `factory/config.py:137`, `factory/dashboard.py:698`, `:846`, `:873`, `factory/onboard.py:223-224`, `factory/stats.py:316` all index `argv[0]`/join lists. Hold.
- Stricter `[workers]` validation (`factory/config.py:292`): previously any TOML value passed via `list(v)`; now empty/non-string argv raises `ConfigError`. Documented examples `README.md:176-178`, `factory/templates/factory.toml:26-27` remain valid. Holds.
- Dashboard snapshot gains `config.worker_when` (`factory/dashboard.py:847`): `factory/dashboard.html:894` iterates `cfg.workers` only, ignores the new key; README documents no per-key schema for `--json` (`README.md:76`). Holds.
- `MENU` text: no consumer outside `factory/manage.py`.
- Host layering: `merge()` (`factory/config.py:203-208`) deep-merges `[defaults.workers.<label>]` tables with a repo `[workers]` array — a table entry survives beside array entries. Holds; template note `factory/templates/factory.toml:39` covers the no-`default` case.

## Required fixes (blocking)

1. **FIX pushes new commits under a stale `factory-approved` label; a fresh REVISE can still merge.**
   `factory/manage.py:104-131` never removes `factory-approved` from the PR. Only the red-CI escalation strips it (`factory/dispatch.py:825-837`); the rebase-conflict and gate-after-rebase escalations (`factory/dispatch.py:724`, `:729`) leave it on, and both reach the manager with a kept worktree on `agent/<n>` (`factory/dispatch.py:720-723`), so the FIX preconditions at `factory/manage.py:109-112` pass. Trigger: such an escalation → manager `FIX` → gate PASS → `git push` (`:124`) → reviewer REVISE → `escalate` (`:130`) with the label still present. Impact: merge stage selects on the label alone (`factory/dispatch.py:803`), so once CI is green and the head contains main (`:857`), code whose latest review was REVISE lands on `main`. This contradicts the diff's own documentation (`README.md:99-100` "Only a fresh APPROVE restores `factory-approved`") and `docs/manager-plan.md:143`/`:284` ("re-add `factory-approved` only on a fresh APPROVE"). Fix: before `worker_round` at `:117`, run the same `gh pr edit <pr> --remove-label factory-approved` used at `factory/dispatch.py:825-837` (needs `number` in the `--json` list at `:108`); `approve_pr` (`:128`) already re-adds it on APPROVE. One call, no new abstraction.

## Optional suggestions (non-blocking)

- `factory/manage.py:124` pushes without `--force-with-lease`, unlike `refresh_pr_branch` (`factory/dispatch.py:731`). The shipped `conflict` profile (`factory/templates/factory.toml:45-47`) rewrites history by design, so unless the worker force-pushes itself the push is rejected → `CalledProcessError` → `mechanism_failure` (`factory/manage.py:222-226`). Fail-closed, and outside the issue's exit gate, but the `conflict` profile cannot complete through FIX as written. Consider `--force-with-lease` on this push.
- `factory/manage.py:65` re-checks `worker in RESERVED_LABELS` though `workers` is already filtered at `:199`. Harmless redundancy.

VERDICT: REVISE

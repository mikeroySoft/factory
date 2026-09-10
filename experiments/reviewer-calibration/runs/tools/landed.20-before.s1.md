## Standards

No `AGENTS.md`/`CONTRIBUTING.md` in this repo; only `pyproject.toml` (`[tool.ruff] line-length = 100`, no `select`, so E501 isn't enforced) and `docs/manager-plan.md` (design plan, not a coding standard). Findings below are smell-baseline judgement calls, none hard violations.

- **Duplicated guard (judgement call)** — `factory/manage.py:199` already strips `RESERVED_LABELS` from `workers` before `parse(proc.stdout, workers)` at `:213`, yet `parse` re-subtracts `RESERVED_LABELS` at `:54` and re-checks `worker in RESERVED_LABELS` at `:65`. Harmless double filter; one site would do. Non-blocking.
- **Duplicated read (judgement call)** — `factory/manage.py:114-115` re-reads all of `events.jsonl` to compute the next attempt number; `manage_pass` already read and filtered the same events for ticket `n` at `:184`. Non-blocking; `apply` doesn't receive them.
- **Long lines** — `factory/config.py:292` (130 chars), `factory/manage.py:109` (136), `:110` (109), `:65` (101) exceed `line-length = 100`. Pre-existing code (`manage.py:53`) already does the same and ruff doesn't select E501, so tooling-unenforced; not a rule breach.
- Pattern reuse is correct: the FIX path (`manage.py:117-130`) mirrors the existing review-bounce flow in `dispatch.process_ticket` (`worker_round` → `escalate` on gate fail → `push` → `review` → `pr_comment` → `approve_pr`/`escalate`). The ticket lock is held for the whole `apply` call (`manage.py:174-182`, `:221`), so the FIX worker round can't race `process_ticket` on `wt-<n>`.

## Spec

Issue #20 / brief (comment) / plan T11, checked against the diff:

| Requirement | Evidence | Status |
|---|---|---|
| `[workers.<label>]` may carry `when` | `factory/config.py:288-298` | Done |
| Table form and legacy array form both load | `config.py:290-291`; test `tests/test_factory.py:147-164` | Done |
| Manager prompt lists labels with their `when` | `manage.py:199-201` | Done |
| ROUTE only for listed labels | `manage.py:54-56`; test `:652-676` (unconfigured `chore` → HUMAN, recorded via `record("manage")` at `:219`; configured → `--add-label chore`) | Done, matches exit gate |
| FIX only for listed labels | `manage.py:63-69`; test `:722-732` | Done |
| FIX on red CI runs the `ci-fix` argv | `manage.py:117-120` → `dispatch.worker_round(n, wt, {data["worker"]}, …)`; test `:678-720` asserts the stub worker ran in `wt-7` and got guidance + packet | Done, matches exit gate |
| Ship `ci-fix` and `conflict` profiles in `templates/factory.toml` | `factory/templates/factory.toml:41-47` | Done (commented, consistent with the template's "defaults are shown commented out" convention at `:2-3`) |
| Profiles remain host-config, human-applied; manager never writes config | `templates/factory.toml:37-39`; no config-writing code in diff | Done |
| Touch `dashboard.py` | `factory/dashboard.py:847` adds `worker_when` to the snapshot | Done (minimal; `dashboard.html:894` doesn't render it) |
| Out of scope: manager writing host config | not present | Respected |

Scope beyond the brief, all traceable to plan T10 (`docs/manager-plan.md:283-291`) which specifies FIX semantics and is the only place FIX is defined: the PR/worktree preconditions (`manage.py:107-112`), push-only-on-PASS, re-review, `factory-approved` only on fresh APPROVE. No net-new abstractions; `RESERVED_LABELS` (`manage.py:32`) just names a set that already existed inline.

Observations, none blocking:

- `manage.py:104-131` returns before the `--remove-label ready-for-human --add-label ready-for-agent` line at `:158-159`, so after a successful FIX+APPROVE the issue keeps `ready-for-human` while the PR gets `factory-approved`. README `:98-99` documents this ("FIX does not merge or requeue the issue") and the merge stage keys on the PR label (`dispatch.py:803`), so behavior is consistent with plan T10 `:290-291`.
- `human_activity` is checked before `apply` (`manage.py:217`) but a FIX round can run up to `budget_min`; a human taking over mid-round isn't detected until the next pass. Same window exists for other decisions, just shorter. Not a documented rule.
- `config.py:292` now rejects a string-valued worker (`default = "omp -p …"`); previously `list(v)` would silently produce a per-character argv. Stricter, not a regression of any documented form (README `:176-178` and template show arrays only).

## Required fixes

None.

## Optional suggestions

1. `factory/manage.py:54`, `:65` — drop the redundant `RESERVED_LABELS` re-checks in `parse` now that the caller pre-filters at `:199`, or keep `parse` self-contained and drop the pre-filter; one site is enough.
2. `factory/dashboard.html:894` — render `cfg.worker_when[label]` next to each worker command so the added snapshot field is visible.
3. `factory/manage.py:114-115` — pass the already-filtered `events` from `manage_pass:184` into `apply` instead of re-reading the journal.

Summary — Standards: 3 judgement-call smells, worst is the duplicated reserved-label filter. Spec: 0 missing/partial requirements, 0 scope creep beyond plan T10; all four exit-gate conditions have direct code and test evidence in the diff.

VERDICT: APPROVE

## Standards

Repo has no AGENTS.md/CONTRIBUTING.md; sources are README.md, `docs/manager-plan.md` (T10/T11), `pyproject.toml` (`[tool.ruff] line-length = 100`, not gate-enforced), and the smell baseline.

No hard violations. Judgement calls:

- **Possible Long Function / Divergent Change** — `factory/manage.py:104-131`: `apply()` now holds a 28-line FIX branch (PR lookup, worktree guards, worker round, push, review, approve/escalate) alongside the label/comment decisions. Extracting `fix(n, issue, data, packet)` would keep `apply` one-reason. Optional; not required by any documented rule.
- Long lines over the configured ruff `line-length = 100`: `factory/config.py:292`, `factory/manage.py:109`. E501 is not in ruff's default rule set and the gate doesn't lint, so not a documented-rule breach; pre-existing code (e.g. `manage.py:55`) already exceeds it. Optional.
- `RESERVED_LABELS` (`factory/manage.py:33`) replaces an inline set used at one site plus two new sites: justified, no new abstraction beyond that.

## Spec

Issue #20 / brief / T11 criteria vs diff:

| Criterion | Status | Evidence |
|---|---|---|
| `[workers.<label>]` may carry `when = "..."`; table-or-array | Met | `factory/config.py:288-298` |
| Manager prompt lists labels with their `when` | Met | `factory/manage.py:199-201` |
| ROUTE only accepted for listed labels | Met | `factory/manage.py:54-57`, `:199`, `:213` (filtered `workers` passed to `parse`) |
| FIX only accepted for listed labels | Met | `factory/manage.py:63-69` |
| ROUTE chore without chore worker → rejected and recorded | Met | `tests/test_factory.py:652-676` (HUMAN decision in `manage` event, no `issue edit`) |
| ROUTE chore with chore worker → label applied | Met | `tests/test_factory.py:674` |
| FIX on red CI runs the `ci-fix` argv | Met | `factory/manage.py:120-123` → `dispatch.worker_round(n, wt, {data["worker"]}, …)` → `cfg.worker()` first-match; `tests/test_factory.py:678-732` |
| Ship `ci-fix` and `conflict` profiles in `templates/factory.toml`, human-applied | Met (commented, matching the template's all-defaults-commented convention at `factory/templates/factory.toml:1-3`) | `factory/templates/factory.toml:41-47` |
| Array-form `[workers]` still loads | Met | `factory/config.py:290-291`, `tests/test_factory.py:147-157` |
| `dashboard.py` touched | Met | `factory/dashboard.py:847` |
| Out of scope: manager writing host config | Respected | no config-writing path; `README.md:102` states it |
| T10 FIX semantics (re-gate, push only on PASS, re-review, `factory-approved` only on fresh APPROVE) | Met | `factory/manage.py:120-130`; test asserts remote ref unchanged on gate FAIL and label only on `gate_ok and APPROVE` (`tests/test_factory.py:727-731`) |

Loop safety checked: a failed FIX calls `dispatch.escalate` (`manage.py:125`,`:130`), which increments `round` (`dispatch.py:369-371`); `manage_pass` refuses rounds above `manager_rounds` (`manage.py:189`). No unbounded FIX→escalate→FIX cycle.

Minor additions beyond the brief, all defensible as guards, not abstractions: `reviewDecision == "CHANGES_REQUESTED"` refusal (`manage.py:109`), stricter argv validation for legacy arrays (`config.py:292` — previously `list(v)` accepted an empty list or a bare string, which would fail later at `expand`).

## Required fixes

None.

## Optional suggestions

1. `factory/config.py:288-298` — unknown keys inside a `[workers.<label>]` table (e.g. `whne = …`) are silently dropped because `KNOWN_KEYS["workers"] is None` skips the subtree in `unknown_keys()`. A typo'd `when` yields "(no when rule)" in the manager prompt with no `doctor` drift warning. Consider rejecting keys other than `command`/`when` in the loader loop (one `if set(entry) - {"command", "when"}: raise ConfigError`).
2. `factory/templates/factory.toml:39` — "Keep a default worker in that layer when configuring workers there" is unnecessary: `merge()` (`config.py:203-208`) is recursive on dicts, so a host-only `[defaults.workers.ci-fix]` table coexists with the repo's `default`. The comment is safe but suggests a constraint that doesn't exist.
3. `factory/manage.py:104-131` — extract the FIX branch into its own function (see Standards).

VERDICT: APPROVE

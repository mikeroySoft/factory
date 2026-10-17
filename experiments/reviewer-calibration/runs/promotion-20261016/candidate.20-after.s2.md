## Standards

Sources: README.md, docs/manager-plan.md (T10/T11), `pyproject.toml`. No AGENTS.md/CONTRIBUTING.md in this repo.

No hard violations. Conventions held:
- Config validation raises `ConfigError` with `{path}: …` prefix like neighbours (factory/config.py:293,295).
- `record("manage", …)` before GitHub mutation preserved (factory/manage.py:219 → :221).
- Host-layer compatibility: `HOST_TABLES` already includes `workers` (factory/config.py:50) and `merge` is recursive on dicts (factory/config.py:203-208), so the `[defaults.workers.<label>]` advice in factory/templates/factory.toml:37-39 is accurate.

Smell notes (judgement calls, non-blocking):
- factory/manage.py:54 `set(workers) - RESERVED_LABELS` is now redundant with the caller's filter at factory/manage.py:199. Harmless defence; leave or drop.

## Spec

| Criterion (issue #20 / brief) | Status | Evidence |
|---|---|---|
| `[workers.<label>]` may carry `when` | ✓ | factory/config.py:289-298 |
| Manager prompt lists labels with `when` | ✓ | factory/manage.py:199-201 |
| ROUTE/FIX accepted only for listed labels | ✓ | factory/manage.py:54, :63-66; tests/test_factory.py:652-676, :743-753 |
| Two profiles (`ci-fix`, `conflict`) in template, human-applied | ✓ | factory/templates/factory.toml:41-47 (commented, host-config) |
| Array-form `[workers]` still loads | ✓ | factory/config.py:290 fallback; tests/test_factory.py:147-157 |
| ROUTE chore rejected/recorded without worker, applied with one | ✓ | tests/test_factory.py:652-676 |
| FIX on red CI runs the selected argv | ✓ | factory/manage.py:118-121; tests/test_factory.py:678-741 |
| Touches dashboard.py | ✓ (minimal) | factory/dashboard.py:847 |
| Out of scope: manager writing host config | respected | no config writes in diff |

Scope note: implementing `FIX` itself is T10 territory (docs/manager-plan.md:283-285), but the issue's exit gate ("a FIX on red CI runs the ci-fix argv") cannot be met without it; the implementation follows T10's spec (one `worker_round` in the kept worktree, re-gate, push, re-review, approve only on fresh APPROVE). Justified.

## Required fixes

### 1. FIX never clears `factory-approved`; a REVISE'd FIX can merge, and the merge stage can rebase the worktree under the running FIX worker

Evidence:
- factory/manage.py:105-131: the FIX branch checks the PR, runs the worker, force-pushes (:124), reviews, and on failure only calls `dispatch.escalate` (:122, :130). Nothing removes `LABEL_APPROVED`. `dispatch.escalate` doesn't either (factory/dispatch.py:375-406).
- Only the CI-red path strips the label (factory/dispatch.py:825-837). The other escalations FIX is designed for keep it: rebase conflict (factory/dispatch.py:722-724) and gate-fail-after-rebase (:727-729) — the exact scenario the new `conflict` profile targets (factory/templates/factory.toml:45-47).
- Merge-stage candidacy is label-only (factory/dispatch.py:803) and does not check the ticket lock; `refresh_pr_branch` operates on the same `wt-{n}` (factory/dispatch.py:720-724).

Trigger A (merges REVISE'd code): rebase-conflict escalation → manager `FIX conflict` → worker resolves → gate PASS → `git push --force-with-lease` (factory/manage.py:124) → reviewer returns REVISE → `escalate` (factory/manage.py:130). PR still carries `factory-approved`, is now current with main, CI goes green on the pushed head → next `land_pass` squash-merges it (factory/dispatch.py:803-912). Contradicts README.md:98-99 ("Only a fresh APPROVE restores `factory-approved`; failure stays with the human") and docs/manager-plan.md:143 (re-add only after gate PASS + fresh APPROVE).

Trigger B (worktree race): the FIX worker runs for up to `budget_min` (factory/manage.py:121) under the ticket lock only; `manage_pass` does not hold `merge.lock` (factory/dispatch.py:764-778 released before factory/dispatch.py:1119). The 10-minute timer pass (README.md:55) sees a labeled, behind-main PR and runs `refresh_pr_branch(n)` → `git rebase` / `git rebase --abort` inside `wt-{n}` while the conflict worker is mid-rebase (factory/dispatch.py:722-723), then re-escalates. Impact: worker state destroyed, spurious escalation round consumed.

Fix (one call, mirrors factory/dispatch.py:825-837): before `worker_round` in the FIX branch, run `gh pr edit agent/{n} --repo REPO --remove-label FACTORY_APPROVED` with `check=False`. `approve_pr` (factory/dispatch.py:673-688) re-adds it on APPROVE. Removing the label drops the PR from merge-stage candidacy, which closes both A and B; no new abstraction needed. No-op in the CI-red case where the label is already gone.

## Optional suggestions

- factory/config.py:289-298: table entries silently ignore unknown keys (e.g. `whne = …`), and `unknown_keys` treats `workers` as opaque (factory/config.py:58), so `factory doctor` won't flag the typo. A `set(entry) - {"command", "when"}` check would surface it; not a documented rule, so non-blocking.
- factory/dashboard.py:847 adds `worker_when` to the snapshot but dashboard.html:894 doesn't render it. Either show it next to the label row or drop the field.

## Changed signatures / defaults — consumer check

- `manage.apply(n, issue, decision, body, data, packet)`: sole caller updated (factory/manage.py:221). No test calls `manage.apply` directly. Holds.
- `manage.parse(output, workers)`: signature unchanged; caller now passes the filtered dict (factory/manage.py:213). Holds.
- `Config.workers` (still `dict[str, list[str]]`): consumers factory/config.py:137, factory/dashboard.py:698, :846, :873, factory/onboard.py:223, factory/stats.py:316 — all index argv lists; hold.
- `Config.worker_when` (new): only consumer factory/dashboard.py:847. Holds.
- `MENU` default text: consumed only by the manager prompt (factory/manage.py:200). Holds.
- Template changes are comments only; no runtime default changed.

VERDICT: REVISE

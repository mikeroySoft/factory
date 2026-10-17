## Standards

Sources: `README.md` (configuration + manager section), `docs/manager-plan.md` §4.3/T11, `factory/templates/factory.toml`. No `AGENTS.md`/`CONTRIBUTING.md` in repo.

No hard violations.

- README updated for the new decision and table form (`README.md:88-102`); template documents the table form and both profiles (`factory/templates/factory.toml:31-47`); tests extended in the existing `tests/test_factory.py` scenario style.
- `apply()` FIX branch reuses `dispatch.worker_round`, `escalate`, `review`, `pr_comment`, `approve_pr` rather than re-implementing the pipeline (`factory/manage.py:117-130`). Matches plan §4.3 "profile chosen by `[workers].when`" and T10's "re-add `factory-approved` only on a fresh APPROVE".
- Judgement-call smell, Duplicated Code (minor): `RESERVED_LABELS` is subtracted in `manage_pass` (`factory/manage.py:199`) and again inside `parse` (`factory/manage.py:54`, `:65`). Harmless; `parse` stays safe when called with the unfiltered dict.

## Spec

Issue #20 / plan T11 criteria vs diff:

| Criterion | Evidence |
|---|---|
| `[workers.<label>]` may carry `when` | `factory/config.py:288-298` parses table-or-array, stores `worker_when` |
| Manager prompt lists labels with `when` | `factory/manage.py:199-201` |
| ROUTE/FIX accepted only for listed labels | `factory/manage.py:54`, `:63-66`; tests `tests/test_factory.py:652-676`, `:736-746` |
| `ci-fix` + `conflict` profiles in template; human-applied | `factory/templates/factory.toml:37-47` (commented, opt-in) |
| Array-form `[workers]` still loads | `factory/config.py:290`; `tests/test_factory.py:147-157` |
| ROUTE chore without worker rejected + recorded; with worker label applied | `tests/test_factory.py:664-676` (HUMAN recorded as `manage` event, no `issue edit`) |
| FIX on red CI runs the selected argv | `factory/manage.py:117-120`; `tests/test_factory.py:678-734` |
| Out of scope: manager writing host config | Not touched |
| `dashboard.py` touched | `factory/dashboard.py:847` exposes `worker_when` in snapshot |

Scope beyond the brief: the FIX decision itself (menu, parse, apply) is new on this branch, not on `origin/main`. Issue #20 lists #19 (T10, where FIX was planned) as a blocker; the diff implements the T10 FIX contract here (`docs/manager-plan.md:283-285`: one `worker_round` in the kept worktree, re-gate, push, re-review, relabel only on fresh APPROVE). Since the issue's exit gate requires "a `FIX` on red CI runs the `ci-fix` argv", the FIX path is required for the acceptance criterion; not scope creep. `CLOSE` from T10 is not implemented, and not required by #20.

## Required fixes

None.

## Optional suggestions

1. `factory/manage.py:104-120` — FIX never fetches before the worker round; `refresh_pr_branch` (`factory/dispatch.py:721`) does `git fetch origin` first. For the `conflict` profile the worker rebases onto whatever `origin/<main>` the last pass left, and the gate's `--base origin/<main>` (`dispatch.py:307`) is evaluated against the same ref. Stale ref → clean rebase/gate here, then `refresh_pr_branch` conflicts again on merge and escalates round 2. One `dispatch.run(["git","fetch","origin"], cwd=wt)` before `worker_round` mirrors the existing path. Not blocking: outcome degrades to a human escalation, not a bad merge (merge stage still requires head ⊇ main tip).
2. `factory/config.py:58` — `KNOWN_KEYS["workers"] = None` means `factory doctor` cannot report a typo like `whenn = "..."` in a worker table. Plan T4 drift reporting only covers `[manager]`, so not a rule; mention only.
3. `factory/dashboard.py:847` — `worker_when` is in the snapshot but `dashboard.html:894` still renders only the argv. Issue names `dashboard.py` only; display is optional.
4. `factory/templates/factory.toml:37-47` — profiles live in the repo-file template but the comment directs copying to host `[defaults.workers.<label>]`. Consistent with plan T11 ("added by a human in host config"); if the intent was `factory init` users enabling them in `.factory.toml` directly, note that uncommenting there also works.

VERDICT: APPROVE

## Review: issue #20 — when-rules, FIX, ci-fix/conflict profiles

### Spec coverage

| Criterion | Evidence | Status |
|---|---|---|
| `when` on `[workers.<label>]`; array form still loads | `factory/config.py:288-298`; `tests/test_factory.py:147-165` | Met |
| Prompt lists labels with `when` | `factory/manage.py:199-201` | Met |
| ROUTE/FIX only for listed labels | `factory/manage.py:54`, `:63-66`, `:213` (parse gets filtered `workers`) | Met |
| ROUTE chore w/o worker rejected + recorded; with worker label applied | parse falls through to HUMAN; `manage` event recorded at `:219`; `tests/test_factory.py:652-675` | Met |
| FIX on red CI runs `ci-fix` argv | `factory/manage.py:104-131`; `tests/test_factory.py:677-721` | Met |
| ci-fix / conflict profiles in template | `factory/templates/factory.toml:31-47` (commented, opt-in) | Met — issue says profiles stay host-config/human-applied; commented block is consistent with that |

Standards: no AGENTS.md/CONTRIBUTING.md at this head; README updated in-diff (`README.md:88-102`) to match behavior. No convention violations found.

### Required fixes

1. **Non-force push after a conflict-profile FIX** — `factory/manage.py:127` (`git push origin agent/{n}`, no `--force-with-lease`).
   Trigger: FIX with `worker: "conflict"`; the shipped `conflict` command (`factory/templates/factory.toml:47`) resolves/restarts a rebase, which rewrites `agent/<n>` history. Plain push is rejected as non-fast-forward → `CalledProcessError` → `manage_pass` records `mechanism_failure` (`:222-225`), with the resolved rebase stranded locally and no PR update. README `:97` promises FIX "pushes only on gate PASS"; the existing merge stage force-pushes after its own rebase (README "How a ticket moves" §6), so the FIX path diverges from the documented rebase→push contract. Defect is in the diff's own feature (the `conflict` profile exists only to be dispatched via FIX). Fix: `--force-with-lease` on the FIX push, or document that `conflict` is not FIX-dispatchable (which contradicts `README.md:95-101`).
   `[INFERENCE]` whether the rebase-conflict escalation leaves the worktree mid-rebase is outside the diff; if it does, `git branch --show-current` is empty and `:111` rejects the FIX before this point, making the `conflict` profile unreachable via FIX entirely — same conclusion either way: the shipped profile has no working dispatch path.

### Optional suggestions

- `factory/manage.py:54`: `set(workers) - RESERVED_LABELS` is now redundant since `manage_pass` already filters (`:199`). Harmless; keep if `parse` is meant to be safe with unfiltered input (tests call it that way at `:725`).
- `factory/config.py:295`: `cfg.workers[label] = command` stores the raw TOML list where the old code copied (`list(v)`). No current aliasing consumer, so not a defect.
- `factory/manage.py:117`: FIX round gets a full `budget_min` deadline for one round. Matches "exactly one round"; just noting it is not bounded by prior attempt consumption.
- `factory/templates/factory.toml:36-38`: comment directs users to `[defaults.workers.<label>]` host config while living in the per-repo `.factory.toml` template. Readers may expect it to be uncommentable in place; wording could say both placements work.

No unrequested abstractions introduced; `RESERVED_LABELS` (`factory/manage.py:33`) replaces an inline set used twice — justified by the new FIX check.

VERDICT: REVISE

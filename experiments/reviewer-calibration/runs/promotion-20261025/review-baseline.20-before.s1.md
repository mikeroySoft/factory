Note: `./review.diff` and `./snapshot` were absent from the sandbox; reviewed `git diff origin/main..HEAD` (07913b4..8dd6588, two commits) against the working tree instead. Issue #20 fetched from GitHub.

## Standards

No `AGENTS.md`/`CONTRIBUTING.md` in the repo; conventions come from `README.md`, `docs/manager-plan.md` (T11), and the template's "defaults shown commented out" convention (`factory/templates/factory.toml:2`).

- Template profiles shipped commented-out (`factory/templates/factory.toml:41-47`) match the template convention and the issue's "host-config, human-applied" note. Consistent.
- README updated for the new `FIX` decision and table-form workers (`README.md:88-99`). Consistent with the documented decision menu.
- `pyproject.toml` sets `ruff line-length = 100` but no gate/CI runs ruff and existing code already exceeds it; not reporting.
- Smell baseline: `RESERVED_LABELS` extraction (`factory/manage.py:33`) replaces an inline set already used twice — reasonable, not speculative. No other smells worth naming.

## Spec (issue #20)

Covered:
- `when` on `[workers.<label>]`, array form preserved, validation (`factory/config.py:288-298`; test `tests/test_factory.py:147-165`).
- Prompt lists labels with `when` (`factory/manage.py:199-201`); ROUTE/FIX restricted to listed, non-reserved labels (`factory/manage.py:54-69`); parse receives the filtered dict (`factory/manage.py:213`).
- Exit gate: ROUTE chore rejected/recorded vs applied (`tests/test_factory.py:652-676`); FIX on red CI runs the `ci-fix` argv (`tests/test_factory.py:678-719`); gate `test` PASS covers `tests/test_factory.py`.
- `dashboard.py` touched as listed (`factory/dashboard.py:847`).

### Required fixes

1. **`conflict` profile cannot complete the FIX path it is shipped for — plain push after a rebase is rejected.**
   - `factory/manage.py:124` pushes with `git push origin agent/{n}` (no force). The shipped `conflict` profile (`factory/templates/factory.toml:45-47`) instructs the worker to "restart [the rebase] against the configured main" and resolve it, i.e. rewrite `agent/<n>` history. `refresh_pr_branch` aborts the failed rebase (`factory/dispatch.py:723`), so the worktree branch equals the remote tip when FIX starts; after the worker's rebase the local branch no longer contains the remote tip and the non-fast-forward push fails deterministically. `dispatch.run` is `check=True` → `CalledProcessError` → caught at `factory/manage.py:222-226` as `mechanism_failure`; no review, no `factory-approved`, worktree left with unpushed work. The existing refresh path already uses `--force-with-lease` (`factory/dispatch.py:731`).
   - Trigger: `DECISION: FIX {"worker":"conflict",...}` on a "rebase onto moved main conflicts" escalation — the exact scenario the issue defines the `conflict` profile for ("resolve the rebase, keep both intents"). The test masks it by stubbing `git push` to `exit 0` (`tests/test_factory.py:705`).
   - Fix: push with `--force-with-lease` at `factory/manage.py:124`, matching `refresh_pr_branch`. No new abstraction needed.

### Optional suggestions

- ROUTE/FIX rejection is recorded only as a `HUMAN` decision with "Unparseable manager output" (`factory/manage.py:72`); the `ValueError` reason ("unknown worker label") is dropped at `factory/manage.py:70-71`. Including it in the HUMAN body would make the "rejected and recorded" evidence legible to the human. Pre-existing behaviour; not blocking.
- `worker_when` is added to the dashboard snapshot (`factory/dashboard.py:847`) but nothing in `dashboard.html` renders it. Harmless; mention only so it isn't mistaken for a wired feature.

## Plan drift

Plan status not recorded; drift judged against the issue's "Touches" list.
- Files changed beyond the named set: `README.md` (documents the new decision; expected by README's menu text), `tests/test_factory.py` (required by the exit gate). Neither exposes a defect.
- Planned proof: `uv run pytest tests/test_factory.py` — gate ran `python -m unittest discover -s tests` PASS; equivalent coverage. Nothing missing from the gate report.

VERDICT: REVISE

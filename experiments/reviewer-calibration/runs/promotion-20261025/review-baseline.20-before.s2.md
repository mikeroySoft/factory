**Inputs:** `./review.diff` and `./snapshot` are absent from the sandbox; reviewed `git diff 07913b4..HEAD` (commits `7fc5fe2`, `8dd6588`) in the checked-out gated revision. Issue #20 + triage comment fetched via GitHub API. No `AGENTS.md`/`CONTRIBUTING.md` in the repo; standards sources are `README.md`, `docs/manager-plan.md` (T11), `factory/templates/factory.toml`.

## Standards

- Config loader convention (`config.py:45-51` host layering, `merge()` at `config.py:203-208`): table-form `[workers.<label>]` merges correctly across host `[defaults.workers.*]` and the repo file; the template note at `factory/templates/factory.toml:37-39` ("keep a default worker in that layer") is accurate because `config.py:286-287` still requires `default`.
- Push convention: the existing re-gate-then-push path uses `--force-with-lease` (`dispatch.py:731`); the new FIX path uses a plain push (`manage.py:124`). See Required #1.
- Smell baseline, judgement calls only: `parse()` still subtracts `RESERVED_LABELS` (`manage.py:54`, `manage.py:65`) after `manage_pass` already pre-filters (`manage.py:199`) — harmless duplication, keeps `parse` safe for direct callers. `import shutil` inside the test body (`tests/test_factory.py:678`) vs. module-level imports elsewhere in the file. No speculative generality: `RESERVED_LABELS` and `worker_when` are each used by ≥2 sites.

## Spec (issue #20 / T11)

| Criterion | Status | Evidence |
|---|---|---|
| `[workers.<label>]` may carry `when` | Met | `config.py:289-298` |
| Manager prompt lists labels with `when` | Met | `manage.py:199-201`; asserted at `tests/test_factory.py:673` |
| ROUTE/FIX only for listed labels | Met | `manage.py:53-56`, `manage.py:63-66`; tests `:652-676`, `:719-729` |
| `ROUTE chore` without chore worker rejected + recorded | Met | HUMAN recorded in `manage` event, no `issue edit` (`tests/test_factory.py:670-676`) |
| With chore worker, label applied | Met | `--add-label chore` (`tests/test_factory.py:674`) |
| FIX on red CI runs `ci-fix` argv | Met | `manage.py:117-120`; `fix-worker` stub output checked (`tests/test_factory.py:706-710`) |
| Array-form `[workers]` still loads | Met | `tests/test_factory.py:147-157` |
| Ship `ci-fix` + `conflict` in template | Met (opt-in) | `factory/templates/factory.toml:41-47`, commented out. Consistent with the template's "defaults are shown commented out" header (`:2-3`) and with "human-applied" in the issue; an active `[workers.ci-fix]` would force an active `default` into every onboarded repo (`config.py:286`). Flagging the interpretation, not blocking. |
| `dashboard.py` touched | Met | `dashboard.py:847` exposes `worker_when`; `dashboard.html` does not render it (optional) |
| Out of scope: manager writing host config | Respected | README `:101-102`, MENU `:33-35` |

## Required fixes

1. **`conflict` profile cannot land through FIX: non-fast-forward push.** `manage.py:124` runs `git push origin agent/{n}`. The rebase-conflict escalation path does `git rebase --abort` before escalating (`dispatch.py:722-724`), so the kept worktree equals the remote branch; the shipped `conflict` command instructs the worker to restart the rebase (`factory/templates/factory.toml:47`), rewriting history. Trigger: manager answers `FIX {"worker":"conflict"}` on that escalation → worker rebases → gate PASS → `manage.py:124` rejected as non-fast-forward → `CalledProcessError` → `manage_pass` records `mechanism_failure` (`manage.py:222-226`). Impact: one of the two profiles this issue ships (scope: "`conflict` (resolve the rebase…)") can never push, review, or re-approve; the rebased worktree is left unpushed. The existing re-gate-then-push path uses `--force-with-lease` (`dispatch.py:731`); use the same here. The gate did not catch it because the test stubs `git push` to `exit 0` (`tests/test_factory.py:699`).

## Optional suggestions

- `dashboard.html:893-894` renders `cfg.workers` but ignores the new `worker_when` key (`dashboard.py:847`); a one-line addition shows the rule next to the label.
- `manage.py:225` labels every FIX failure (precondition `ValueError` at `:109-112`, worker/gate errors) as `github_command_failed`; the audit reason is now misleading for FIX.
- `docs/manager-plan.md:347` says the manager "may recommend one [profile] in a HUMAN diagnosis"; `MENU` (`manage.py:17-32`) doesn't say so. Prompt-only change.
- Drop the redundant `- RESERVED_LABELS` / `worker in RESERVED_LABELS` in `parse` (`manage.py:54`, `:65`) or the pre-filter at `manage.py:199`, not both.

## Plan drift

- Worker plan: not recorded. Against the issue's **Touches** list (config.py, manage.py, dashboard.py, templates/factory.toml), the diff also changes `README.md` (documents the new `FIX` decision and table-form workers; expected given README `:85-98` is the manager spec) and `tests/test_factory.py` (exit gate requires it to pass). Neither exposes a defect.
- Proof: gate `test: PASS` runs `python -m unittest discover -s tests` (`.factory.toml:4-13`), covering `tests/test_factory.py`. All four exit-gate conditions have a test. Missing proof: no test exercises a real push after a rebase (see Required #1).

VERDICT: REVISE

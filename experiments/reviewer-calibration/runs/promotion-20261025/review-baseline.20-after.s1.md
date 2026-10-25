## Standards

Repo has no AGENTS.md/CONTRIBUTING.md; conventions come from README.md, `docs/manager-plan.md`, and the template header ("defaults are shown commented out", `snapshot/factory/templates/factory.toml:2`).

- README updated in step with behaviour (`review.diff:10-24`); manager MENU updated (`review.diff:88,95-97`). Consistent with the existing "docs describe the decision menu" pattern.
- Host-layer config: `merge` is recursive on dicts (`config.py:203`), so `[defaults.workers.ci-fix]` tables layer under a repo `[workers]` as the template comment claims (`review.diff:214-216`). `KNOWN_KEYS["workers"] = None` means `factory doctor` does not flag table-form entries as drift (`config.py:219`).
- Smell baseline (judgement calls only):
  - *Possible Feature Envy*: `apply`'s FIX branch is a 27-line dispatch pipeline (`review.diff:139-166`) living in `manage.py`, reaching into `dispatch.worker_round/escalate/review/pr_comment/approve_pr`. It matches the plan's "dispatch one `worker_round` in the kept worktree" (`docs/manager-plan.md:283`), and `manage.py` already wraps `dispatch` everywhere, so suppressed.
  - `RESERVED_LABELS` extraction (`review.diff:102,112,122,176`) removes a duplicated literal set; net reduction, not a new abstraction.

No hard standards violations.

## Spec (issue #20 + brief)

| Criterion | Status | Evidence |
|---|---|---|
| `[workers.<label>]` may carry `when` | Met | `review.diff:36,45-55` |
| Manager prompt lists labels with their `when` | Met | `review.diff:176-178` |
| ROUTE only for listed labels | Met | `review.diff:112-115,187`; test `review.diff:263-286` covers both halves of the exit gate (no `chore` → HUMAN recorded, no `issue edit`; with `chore` → `--add-label chore`) |
| FIX only for listed labels | Met | `review.diff:120-126`; test `review.diff:349-359` rejects `default`, `ready-for-agent`, unconfigured `ci-fix`, non-string |
| FIX on red CI runs the `ci-fix` argv | Met | `review.diff:152-155`; test `review.diff:288-347` asserts the configured `fix-worker` ran, guidance + packet reached it, push/approve only on gate PASS + APPROVE |
| `ci-fix` and `conflict` profiles in `templates/factory.toml` | Met (commented) | `review.diff:218-224` |
| Array-form `[workers]` still loads | Met | `review.diff:47,236-248` |
| Out of scope: manager writing host config | Respected | no config writes in diff |

Correctness checks on the FIX path (no defects found):
- Push gated on gate PASS (`review.diff:156-159`); `factory-approved` only on fresh APPROVE (`review.diff:162-165`). Merge stage keys off the PR label, not issue labels (`dispatch.py:748-751`), so the approved PR merges next pass even though the issue stays `ready-for-human`.
- Preconditions reject missing worktree, closed PR, head mismatch, wrong checked-out branch (`review.diff:144-147`); failures surface as `mechanism_failure` via the existing `except` (`review.diff:197-199`), and the recorded `manage` event prevents replay for that round.
- Gate-fail path calls `escalate`, which records a new round — bounded by `manager_rounds` (`manage.py:189`).
- Worker labels passed to `parse` already exclude reserved labels, and FIX re-checks `RESERVED_LABELS` (`review.diff:122`) — belt-and-braces, harmless.

## Plan drift

Plan status not recorded; comparing against the issue's **Touches** list:
- Files changed not named: `README.md` (`review.diff:1`), `tests/test_factory.py` (`review.diff:228`). Both are the doc/test counterparts of the named changes; no defect exposed.
- Planned proof: issue's `uv run pytest tests/test_factory.py` → gate `test: PASS`. Nothing missing.

## Required fixes

None.

## Optional suggestions

1. **Profiles are commented out** (`review.diff:218-224`). Issue says "Ship two default profiles"; the template convention shows defaults commented (`factory.toml:2`), but unlike `default`/`chore`, `ci-fix`/`conflict` are not `Config` defaults, so a fresh `factory init` repo can never select them for FIX until a human uncomments or adds host config. README (`review.diff:23-24`) documents this honestly. If "default" was meant as active, add them to `Config.workers` default_factory; otherwise this is fine as-is.
2. `reviewDecision == "CHANGES_REQUESTED"` precondition (`review.diff:144`) is not mentioned in README's "open PR" wording (`review.diff:19`). One clause would keep docs aligned.
3. `worker_when` is exposed in the snapshot (`review.diff:67`) but `dashboard.html:894` still renders only the argv; surfacing the rule is a one-line `dl` entry if wanted.

Summary — Standards: 0 violations, 1 suppressed smell. Spec: all exit-gate criteria met; worst item is the ambiguity on whether shipped profiles should be active (non-blocking).

VERDICT: APPROVE

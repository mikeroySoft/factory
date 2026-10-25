## Standards

No `AGENTS.md`/`CONTRIBUTING.md` in the snapshot; documented conventions are `README.md`, `skills/factory/SKILL.md`, `docs/manager-plan.md`, and `pyproject.toml` (`[tool.ruff] line-length = 100`).

- `README.md:10-24` — manager decision docs updated for `FIX` and `[workers.<label>]` tables; matches the "How a ticket moves"/"Configuration" sections' existing style.
- `factory/templates/factory.toml:208-224` — profiles shipped commented out, consistent with the template header ("defaults are shown commented out", `snapshot/factory/templates/factory.toml:2-3`) and the plan's "profiles are still added by a human in host config" (`docs/manager-plan.md:346-347`).
- `factory/manage.py:102,112,176` — `RESERVED_LABELS` replaces the inline set; same filter now applied to both the prompt and `parse`. No smell-baseline hits beyond judgement calls below.

Judgement calls (non-blocking):
- `factory/config.py:49` — ~129 chars vs `line-length = 100`. Ruff's default rule set does not select E501 and pre-existing lines already exceed it (`snapshot/factory/manage.py:166,211`), so not a tooling-enforced breach.
- `factory/manage.py:112` — `set(workers) - RESERVED_LABELS` is now redundant: `manage_pass` already pre-filters `workers` at `factory/manage.py:176` before calling `parse` at `:187`. Harmless duplication.

## Spec (issue #20 + triage brief)

| Criterion | Evidence | Status |
|---|---|---|
| `[workers.<label>]` may carry `when` | `factory/config.py:36,45-55` | met |
| Manager prompt lists labels with their `when` | `factory/manage.py:176-178` | met |
| ROUTE/FIX accepted only for listed labels | `factory/manage.py:112-115,120-123,176,187` | met |
| `ci-fix` and `conflict` profiles in `templates/factory.toml` | `factory/templates/factory.toml:218-224` | met (opt-in, per plan T11) |
| Array-form `[workers]` still loads | `factory/config.py:47-48`; `tests/test_factory.py:236-248` | met |
| Exit gate: ROUTE chore rejected+recorded without worker, applied with one | `tests/test_factory.py:263-286` (`manage` event decision HUMAN vs ROUTE, `--add-label chore`) | met |
| Exit gate: FIX on red CI runs the `ci-fix` argv | `factory/manage.py:139-166`; `tests/test_factory.py:288-347` | met |
| Out of scope: manager writing host config | no config writes in diff; `README.md:24` states it | met |
| `dashboard.py` touched | `factory/dashboard.py:67` exposes `worker_when` | met |

FIX implementation traced against `docs/manager-plan.md:283-285` ("dispatch one `worker_round` in the kept worktree with the guidance as `extra`, re-gate, push, re-review; re-add `factory-approved` only on a fresh APPROVE"): `factory/manage.py:151-155` (guidance+packet as `extra`), `:156-158` (gate FAIL → escalate, no push), `:159` (push only after PASS), `:160-165` (review → `approve_pr` only on APPROVE, else escalate). `Config.worker` picks the first configured key in the label set (`snapshot/factory/config.py:137`), so `{data["worker"]}` at `:153` selects exactly the named profile. Early `return` at `:166` skips the `ready-for-agent` relabel at the function tail, matching README "FIX does not merge or requeue the issue".

Scope beyond the brief (not defects):
- `factory/manage.py:144` — rejects FIX when `reviewDecision == "CHANGES_REQUESTED"`. Not in the issue; mirrors `merge_pass_locked` (`snapshot/factory/dispatch.py:805`) so a human's requested-changes isn't overridden by an automated push. Reasonable guard; no new abstraction.

## Plan drift

Plan status not recorded, so no plan-named file list or planned proof to diff against. Relative to the issue's **Touches** list, the diff additionally changes `README.md` and `tests/test_factory.py` — docs for the new decision/config form and the exit-gate tests the issue requires. The gate report shows `test: PASS`, which covers the issue's verification command (`uv run pytest tests/test_factory.py`). No required defect exposed.

## Required fixes

None.

## Optional suggestions

1. `factory/manage.py:112` — drop `- RESERVED_LABELS` since the caller pre-filters (`:176`); or keep `parse` self-defending and drop the caller filter. One site is enough.
2. `factory/config.py:49` — wrap to ≤100 chars to match `[tool.ruff] line-length`; only matters if E501 is ever enabled.
3. `factory/config.py:47` — unknown keys inside a `[workers.<label>]` table (e.g. a `when` typo) are silently ignored and `unknown_keys` skips `workers` (`snapshot/factory/config.py:58,219`); a `doctor` drift line would surface misconfigured `when` rules. Not required by the issue.

VERDICT: APPROVE

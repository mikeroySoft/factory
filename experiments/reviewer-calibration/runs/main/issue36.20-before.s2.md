# Review: issue #20 — `when` rules, FIX decision, `ci-fix`/`conflict` profiles

## Spec coverage

| Acceptance criterion | Evidence | Status |
|---|---|---|
| `[workers.<label>]` may carry `when` | `factory/config.py:288-298` parses dict-or-list entries, validates `command` argv and `when` text, stores `cfg.worker_when` | Met |
| Manager prompt lists labels with `when` | `factory/manage.py:199-201` emits `- label: <when or '(no when rule)'>` for non-reserved workers | Met |
| ROUTE/FIX only for listed labels | `factory/manage.py:213` passes the filtered `workers` dict to `parse`; `manage.py:54`, `manage.py:63-66` reject unknown/reserved labels | Met |
| ROUTE chore rejected+recorded without worker; label applied with one | `tests/test_factory.py:652-675` — HUMAN decision recorded in `manage` event, no `issue edit`; configured case asserts `--add-label chore` and the `when` line in the prompt | Met |
| FIX on red CI runs `ci-fix` argv | `manage.py:104-131` dispatches one `worker_round` with `{data["worker"]}`; `tests/test_factory.py:677-717` exercises gate fail / REVISE / APPROVE, asserts push only on gate PASS and `factory-approved` only on fresh APPROVE | Met |
| Array-form `[workers]` still loads | `config.py:290-291` list path; `tests/test_factory.py:147-165` mixes array `default` with table `chore` | Met |
| Two profiles shipped in template | `factory/templates/factory.toml:31-47` — commented-out `ci-fix` and `conflict` tables | Met (see note 1) |
| Touches limited to named files (+README/tests) | diff | Met |
| Manager cannot write host config | `README.md:97-98`, `factory.toml:34-35`; no code path writes config | Met |

Gate: test PASS on target host; no conflict markers; leak-scan PASS.

## Required fixes

None.

## Optional suggestions (non-blocking)

1. **Profiles are commented out** (`factory/templates/factory.toml:41-47`). The issue says "Ship two default profiles" and the triage comment says "human-applied refers to runtime application, not implementation." Commented-out entries are a defensible reading (uncommenting in `.factory.toml` would activate them repo-wide, and the template comment at `:37-39` correctly explains that `[defaults.workers]` replaces the layer wholesale so `default` must be kept). If the author intended live-but-inert entries, that would be a deliberate policy change; not required by the text.

2. **Redundant reserved-label filtering** (`factory/manage.py:54`, `:65`). `manage_pass` already strips `RESERVED_LABELS` at `:199` before calling `parse`, so the `- RESERVED_LABELS` / `worker in RESERVED_LABELS` checks inside `parse` are defensive duplicates. Harmless; keep only if `parse` has other callers passing raw `cfg.workers`.

3. **FIX preconditions don't check that CI is red** (`factory/manage.py:104-110`). The exit gate phrases FIX as "on red CI", but the code accepts FIX for any open, non-CHANGES_REQUESTED PR in a kept worktree. This is consistent with the README wording at `README.md:97-104` (FIX is a generic one-round worker dispatch), and the manager selects the worker from the `when` list. Only worth tightening if you want code, not the model, to enforce the red-CI trigger. [INFERENCE] The manager may legitimately FIX a review-escalated PR with a different worker.

4. **Concurrency with the merge stage** (`factory/manage.py:104-131`). FIX runs a worker and force-free push on `agent/<n>` inside the manage pass. Whether `manage_pass` holds the per-ticket flock around `apply` is outside the diff; README says manager executions use ticket-lock waits, and the PR lost `factory-approved` on red CI so the merge stage won't rebase/force-push it concurrently. No evidence of a defect; noting the assumption.

5. **`config.py:292` line length** exceeds the surrounding style. Style only; no documented rule.

No unrequested abstractions introduced: `RESERVED_LABELS` (`manage.py:33`) replaces an inline set literal reused in two branches; `worker_when` is a flat dict rather than a worker-entry type, which is the simpler option.

VERDICT: APPROVE

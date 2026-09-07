# Review: #20 — when-rules, FIX, ci-fix/conflict profiles

## Spec

| Criterion | Evidence | Status |
|---|---|---|
| `[workers.<label>]` table with optional `when` | `factory/config.py:289-298` parses dict-or-array entries, validates argv and `when` type, stores `worker_when` | met |
| Array-form `[workers]` still loads | `factory/config.py:290` (`entry` used as argv when not a dict); `tests/test_factory.py:147-158` covers coexisting forms | met |
| Manager prompt lists labels with `when` | `factory/manage.py:199-201` emits `- <label>: <when or "(no when rule)">` for non-reserved workers | met |
| ROUTE/FIX only for listed labels | `factory/manage.py:63-69` (FIX rejects non-str, unlisted, reserved); ROUTE at `:54`; parse receives the filtered map at `factory/manage.py:213` | met |
| `ROUTE chore` rejected + recorded without a chore worker; label applied with one | `tests/test_factory.py:652-675` asserts `manage` event decision HUMAN vs ROUTE and `--add-label chore` | met |
| FIX on red CI runs `ci-fix` argv | `factory/manage.py:104-131`; `tests/test_factory.py:677-731` exercises gate fail / REVISE / APPROVE / rebase cases, checks push only on gate PASS and `factory-approved` only on APPROVE | met |
| `dashboard.py` touched | `factory/dashboard.py:847` exposes `worker_when` in snapshot | met |
| Two profiles in `templates/factory.toml` | `factory/templates/factory.toml:41-47` — present but commented out | see note |

**Note (non-blocking):** the issue says "Ship two default profiles"; the diff ships them as commented-out examples with copy instructions (`factory/templates/factory.toml:37-39`). A repo initialized from this template will reject `FIX {"worker":"ci-fix"}` until a human uncomments/copies the block. The issue's own clause "remain host-config, human-applied" and the triage brief ("these remain host-config that humans apply") support the opt-in reading, and the README change at `README.md:99-100` documents it explicitly. Ambiguity resolved in favor of the triage brief; flagging so the author can confirm intent.

## Standards (README)

- README updated for the new decision and worker forms: `README.md:88`, `README.md:93-101`. Consistent with "Configuration" section's claim that the template documents every key.
- FIX honors documented human veto ("requesting changes on the PR blocks any merge"): `factory/manage.py:109` refuses when `reviewDecision == "CHANGES_REQUESTED"`.
- Manage event recorded before GitHub mutation, as README states: `factory/manage.py:219` precedes `apply` at `:221`.
- Worktree/branch invariants (`.factory/wt-<n>` on `agent/<n>`) enforced at `factory/manage.py:106,109-112`.

## Observations (no action required)

- `factory/manage.py:54` still subtracts `RESERVED_LABELS` from `workers`, which `manage_pass` already filtered at `:199`. Harmless; `parse` stays safe for direct callers.
- `factory/templates/factory.toml:39` warns that the `[workers]` layer needs a `default` (enforced at `factory/config.py:286-287`). Uncommenting only `[workers.ci-fix]` in the repo template would fail config load with a clear error; documented, so acceptable.
- Uncommenting `[workers.conflict]` alongside an active `chore` default: `factory/config.py:288` replaces the whole map, dropping the built-in `chore` argv — pre-existing behavior, unchanged by this diff.

VERDICT: APPROVE

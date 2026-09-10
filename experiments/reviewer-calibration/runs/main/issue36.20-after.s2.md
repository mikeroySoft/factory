# Review: issue #20 — when-rules, ci-fix/conflict profiles

## Spec coverage

| Criterion | Evidence | Status |
|---|---|---|
| `[workers.<label>]` may carry `when` | `factory/config.py:288-298` parses table-or-array, stores `worker_when` | met |
| Manager prompt lists labels with `when` | `factory/manage.py:199-202` (`- {label}: {when or '(no when rule)'}`) | met |
| ROUTE/FIX only for listed labels | `factory/manage.py:63-69` (FIX), `:54` (ROUTE); `parse` receives reserved-filtered `workers` at `:213` | met |
| ROUTE chore rejected + recorded without worker | `manage.py:213` → HUMAN; `dispatch.record("manage", …)` at `:219` precedes `apply`; `tests/test_factory.py:652-676` | met |
| FIX on red CI runs `ci-fix` argv | `manage.py:104-131`; `tests/test_factory.py:678-736` | met |
| Array-form `[workers]` still loads | `config.py:290-291` (`entry` non-dict path); `tests/test_factory.py:147-165` | met |
| Two profiles in `templates/factory.toml` | `factory/templates/factory.toml:40-46` | met (as commented opt-in; see O1) |
| Touches limited to named files | config/manage/dashboard/template + README/tests | met |

## Required fixes

None.

## Optional suggestions

- **O1** `factory/templates/factory.toml:40-46` — profiles are shipped commented-out. The issue says "ship two default profiles" and "remain host-config, human-applied"; the diff reads the second clause as opt-in examples. Defensible, but the template comment directs users to `[defaults.workers.<label>]` in host config with "keep a default worker in that layer" (`:37-38`), which only makes sense if host-layer `[workers]` replaces rather than deep-merges the repo table (`config.py:286` raises without `default`). Confirm host/repo merge semantics match that instruction; the diff cannot show it. [INFERENCE]
- **O2** `factory/manage.py:215-217` — the `human_activity` re-check runs before `apply`. FIX now spends a full worker round (`budget_min`) inside `apply` (`:114-117`), so a human taking over during that window is not detected before the push/approve at `:121-125`. Pre-existing gap for other decisions, but FIX widens it from seconds to minutes. Not in the issue's acceptance criteria; note only.
- **O3** `factory/config.py:297` stores the parsed TOML list without copying (previously `list(v)` at old `:287`). Harmless today; mention in case anything mutates argv in place.
- **O4** `factory/manage.py:101-105` — a missing PR makes `gh pr view` raise `CalledProcessError`, surfacing as `mechanism_failure` with a stack-derived reason rather than the explicit `ValueError` message at `:107`. Same terminal effect; a clearer diagnosis would be nicer, not required.

No unrequested abstractions introduced; `RESERVED_LABELS` (`manage.py:32`) replaces an inline set now used in three places.

VERDICT: APPROVE

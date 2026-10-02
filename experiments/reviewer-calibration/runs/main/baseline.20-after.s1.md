## Standards (README conventions)

- README updated for `FIX`, table-form workers, and `when` listing (README.md:88-102). Matches implemented behavior: labels filtered by `RESERVED_LABELS` before prompt and parse (factory/manage.py:199-202, 213), push only after gate PASS (factory/manage.py:121-125), approval only on fresh APPROVE (factory/manage.py:128-131).
- `manage` event still recorded before `apply` (factory/manage.py:219-221), consistent with the README's "records a `manage` event before GitHub mutations".
- Config remains single source of worker values; dashboard exposes `worker_when` alongside `workers` (factory/dashboard.py:847).

No convention violations found.

## Spec (issue #20)

| Criterion | Evidence |
|---|---|
| `when` on `[workers.<label>]` | factory/config.py:103, 290-297 |
| Prompt lists labels with `when` | factory/manage.py:200-202 (`(no when rule)` fallback for array-form) |
| ROUTE/FIX accept only listed labels | factory/manage.py:54, 63-66; parse receives the filtered `workers` dict (factory/manage.py:213) |
| ROUTE chore rejected+recorded without worker; label applied with one | tests/test_factory.py:652-676 |
| FIX on red CI runs the selected argv | factory/manage.py:104-131; tests/test_factory.py:678-736 |
| Array-form `[workers]` still loads | factory/config.py:290 (`entry` used directly when not dict); tests/test_factory.py:147-157 |
| `ci-fix` / `conflict` profiles in template | factory/templates/factory.toml:31-47 |
| Touches config/manage/dashboard/template | all present |

Non-blocking note: the two profiles are shipped commented out (factory/templates/factory.toml:41-47). The issue says "ship two default profiles" but also "remain host-config, human-applied", and the template text directs copying into `[defaults.workers.<label>]`; the commented form is consistent with the human-applied constraint. `[INFERENCE]` that `[defaults.workers]` host-layer exists — not in the packet.

`[INFERENCE]` `dispatch.worker_round`, `dispatch.review`, `dispatch.approve_pr`, `dispatch.gh_json` signatures (factory/manage.py:105-131) are not in the diff; the passing gate is the only evidence they match.

VERDICT: APPROVE

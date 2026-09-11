## Standards

### Required
None.

### Optional
- `factory/manage.py:65` — `or worker in RESERVED_LABELS` is unreachable: `parse()` receives the pre-filtered `workers` dict from `factory/manage.py:199`, so `worker not in workers` already excludes reserved labels. Dead clause; harmless.
- `factory/manage.py:125` — `dispatch.review()` runs under the `manage` execution scope without setting `execution.review_round`, unlike both callers in `factory/dispatch.py:1039,1054`. The nested review scope inherits `review_round=None`, which `lifecycle.py:438-439` accepts as valid, and `stats.py:233` counts review rounds from PR comments, not this field. Convention divergence in the events journal, not a documented rule.
- `factory/dashboard.py:847` — `worker_when` joins the snapshot but nothing in `dashboard.html` renders it (same as the pre-existing `workers` key at `:846`). Consistent with prior convention; flagging only because T11 lists `dashboard.py` as a touch point.
- `factory/config.py:58` (`"workers": None` in `KNOWN_KEYS`) — a typo'd sub-key inside `[workers.<label>]` (e.g. `whne`) is silently dropped rather than reported as drift by `factory doctor`. Pre-existing gap widened by the new table form; no rule requires it.

Verified wiring: `worker_round(n, wt, {label}, title, extra, attempt, deadline)` at `factory/manage.py:117-120` matches `factory/dispatch.py:915-922`; `Config.worker` (`factory/config.py:135-138`) resolves the single-element label set to the FIX worker argv; `escalate(n, reason, logfile)` and `review(wt, n, report)` match their signatures; `packet` is `is_file()`-checked at `factory/manage.py:194` before `apply()`; the ticket lock (`factory/manage.py:174-182`) is held for the worker round; `escalate()` never removes `wt-<n>`, so the red-CI FIX precondition is reachable; merge stage keys on the PR label only (`factory/dispatch.py:803`), so leaving the issue `ready-for-human` after APPROVE does not block merge.

## Spec

### Required
None. Each criterion of issue #20 is met:

- "`[workers.<label>]` may carry `when`" / "Existing array-form `[workers]` still loads" — `factory/config.py:288-298` handles dict-or-list per label; `factory/dashboard.py:847` exposes it. Host-layer `merge()` (`factory/config.py:203-208`) recurses on tables and replaces on lists, so mixed forms across layers resolve deterministically.
- "The manager prompt lists labels with their `when`" — `factory/manage.py:200-201`.
- "`ROUTE` and `FIX` are only accepted for listed labels" — `factory/manage.py:53-57` (ROUTE) and `:63-69` (FIX), both against the reserved-filtered dict; unparseable/invalid → HUMAN with the `manage` event recorded at `:219` before `apply()`.
- "a `FIX` on red CI runs the `ci-fix` argv" — `factory/manage.py:104-131`: precondition on kept worktree + open PR, worker round with guidance + packet as `extra`, re-gate, push only on PASS, re-review, `approve_pr` only on fresh APPROVE, else `escalate`. Matches plan T10/T11 (`docs/manager-plan.md:283-285,341-351`).
- "Ship two default profiles ... remain host-config, human-applied" — `factory/templates/factory.toml:37-47` as commented profiles with copy-to-host instructions; nothing in the diff writes host config.
- Tests cover ROUTE-without/with-`chore`, FIX gate×verdict matrix, and reserved/unlisted FIX rejection (`tests/test_factory.py:652-731`).

### Optional
- Scope note: the full FIX `apply()` path (not just label gating) is net-new here, not pre-existing — but the exit gate "a `FIX` on red CI runs the `ci-fix` argv" requires it, so this is in scope, not creep.

Summary — Standards: 0 required, 4 optional (worst: unused `review_round` in FIX-triggered review scope). Spec: 0 required, 1 optional note.

VERDICT: APPROVE

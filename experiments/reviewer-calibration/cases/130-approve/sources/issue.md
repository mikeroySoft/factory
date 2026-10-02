# Issue #130: Idle dispatcher passes write no lifecycle rows for parked tickets

**Scope**
A dispatcher pass that finds nothing to do for a parked ticket appends no lifecycle rows for it. Today every 10-minute pass opens a `lifecycle.scope(EVENTS, "manage", ticket=n)` for each escalated/`ready-for-human` ticket and writes ~10 rows (`enter`, `resource_requested`, `lock_acquired`, `handoff`, `child_start`/`child_exit`, `lock_released`, `exit`) of ~800 bytes each, each repeating the full `process`/`resource` blocks, even when nothing changed. `landing` does the same with `child_start`/`child_exit`/`handoff`.

Evidence (rocm-cli, 50 MB tail sample, ~56 h, 0 active tickets, 9 `ready-for-human`): `manage` lock acquire/release/request = 45% of bytes, `manage` enter/exit = 18%, `landing` child/handoff = 11%. ≈21 MB/day while idle; the file reached 393 MB since 2026-09-03 (factory 182 MB, district 172, rocm-app 110, gpuflo 91). `factory dashboard --json` for rocm-cli takes 28.6 s / 633 MB, right at District's 30 s status timeout.

**Touches**
- `factory/manage.py` scopes at ~423, ~590, ~722 and `factory/handoff.py` ~161: decide "no-op" before opening the scope, or have the scope drop its rows when it exits without a `record()`/state change.
- `factory/dispatch.py` `landing` scope (~1346).
- `factory/lifecycle.py` if the no-op suppression belongs in `scope()`.

**Exit gate**
- A new test runs the manage/landing path for a parked ticket whose state did not change and asserts `events.jsonl` gains no rows for that ticket; a pass that does act (new decision, lock contention, failure) still writes its full lifecycle trail.
- Existing lifecycle/reconciliation tests unchanged and passing: `uv run --frozen --no-managed-python python -m unittest discover -s tests`.

**Out of scope**
- Rotating, truncating or migrating existing `events.jsonl` files (separate ticket).
- Changing `record()` audit rows (`claimed`, `attempt`, `review`, `manage`, `escalate`, `human-decision`, …) or their schema.

## Comment by @mikeroySoft (2026-09-30T02:45:02Z)

Triage: The issue has a clear problem statement (idle dispatcher passes write ~21 MB/day of redundant lifecycle rows for parked tickets) and a fully specified, observable done-condition: a new test asserting no rows are added for an unchanged parked ticket while active passes still write their full trail, plus the constraint that existing lifecycle/reconciliation tests remain unchanged and passing. Specific files and line numbers are named (factory/manage.py ~423/~590/~722, factory/handoff.py ~161, factory/dispatch.py ~1346, factory/lifecycle.py scope()), and out-of-scope items are explicitly excluded. It does not touch release, signing, or security policy, and its blast radius is contained to this repository, so it does not require human design judgment.

Agent brief: Suppress lifecycle scope rows when a dispatcher pass is a no-op for a parked ticket. In factory/manage.py (scopes ~423, ~590, ~722), factory/handoff.py (~161), and factory/dispatch.py landing scope (~1346), decide 'no-op' before opening the scope, or have factory/lifecycle.py's scope() drop its rows when it exits without a record()/state change. Do NOT change record() audit rows (claimed, attempt, review, manage, escalate, human-decision, ...) or their schema, and do NOT rotate/truncate/migrate existing events.jsonl. Add a new test that runs the manage/landing path for a parked ticket whose state did not change and asserts events.jsonl gains no rows for that ticket, while a pass that does act (new decision, lock contention, failure) still writes its full lifecycle trail. Verify with: uv run --frozen --no-managed-python python -m unittest discover -s tests

## Review: `origin/main..HEAD` vs issue #62

Callers of changed surfaces (outside the diff):
- `Config.manager_cmd(prompt_path, cwd)` — only callers are `factory/manage.py:265` and `factory/learn.py:68`, both updated in the diff. No others (`grep manager_cmd`).
- `escalate` events with `reason="manager_failed"` — consumers: `factory/dispatch.py:245-248` (lifecycle outcome; fine), `factory/manage.py:237-243` (latest-escalation lookup; dedupes via the `manage` round event — see R1 for the gap), `factory/stats.py:167-171` (counts every `escalate` as an escalation — see O3).
- `escalation_packet` round arithmetic (`dispatch.py:375-378`) — callers `dispatch.py:385` and `dispatch.py:587` unchanged; both still hold.
- Doctor row relabel `manager: <exe>` → `manager command` — only consumer is `tests/test_factory.py:410`, updated. Dashboard/stats don't read doctor rows.
- `DEFAULT_MANAGER` named in the brief does not exist on `origin/main` (`manager: list[str] | None = None`, `config.py:105`); the "default becomes worker shape" criterion is satisfied by the template (`factory.toml:65`) and README (`README.md:216`).

## Required fixes

**R1. `manager_failed` event is recorded before the post-run `human_activity` check, so a human-owned ticket gets the manager re-run on the next pass.**
Evidence: `factory/manage.py:272-273` records the `escalate`/`manager_failed` event; `factory/manage.py:281-282` then `continue`s on human takeover *without* recording the `manage` event at `:286`. Next pass, `manage.py:237` selects the `manager_failed` event as the latest escalation (its `at` is later), `:241-243` finds no `manage` row for that round, and `human_activity(n, escalation)` at `:246` now compares against the `manager_failed` timestamp (`manage.py:136`), so any human comment/label made during the failed run is ignored → manager re-runs, and on success posts a decision onto a ticket the human already took over. On `origin/main` this scenario was guarded (latest escalation stayed the original one, `human_activity` returned True). Trigger: manager exits nonzero while a human touches the issue. Impact: repeated manager invocations + a HUMAN/decision comment on a human-owned ticket — exactly what the `human_activity` guard exists to prevent (CHANGELOG 0.3.0 "Manager stage": untouched packets only).
Fix: move the `dispatch.record("escalate", …, reason="manager_failed")` call below the `human_activity` check at `:281`, i.e. next to the `manage` record at `:286` (event order escalate→manage is preserved; `test_manager_failure_bounds_stderr_and_records_reason` is unaffected).

## Optional suggestions

**O1. Template/README claim rejection that only exists for the manager.** `factory/templates/factory.toml:25` (under `[workers]`) and `README.md:100` say bare `omp {prompt}` "is rejected"; the check lives only in `manager_cmd` (`config.py:149-150`). `Config.worker` (`config.py:138-141`) and the worker doctor rows (`onboard.py:223-224`) do no such check. Drop "and is rejected" from the worker comment or qualify it as manager-only.

**O2. Unrequested example change.** `factory/templates/factory.toml:44,48` replace the `ci-fix`/`conflict` example commands (which embedded the path in an instruction string and were valid) with plain `@{prompt}`, dropping the profile-specific instruction text. Not in the brief; the examples now duplicate `default`.

**O3. `factory stats` counts `manager_failed` as an escalation.** `stats.py:167-171` counts all `escalate` events, so `escalation_count`/`escalation_times` inflate by one per manager failure. The issue wants stats to "count it", so this may be intended; if not, filter `reason != "manager_failed"` there as `dispatch.py:376` does.

**O4. Duplicated omp check.** `onboard.py:234-235` repeats `config.py:149-150`. Doctor could call `cfg.manager_cmd(Path("p"), Path("c"))` inside `try/except ConfigError` to keep one source of truth. Judgement call.

**O5.** `CHANGELOG.md` not updated; no documented rule requires it, but every 0.3.0 manager change is listed there with `(#N)`.

VERDICT: REVISE

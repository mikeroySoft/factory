# Issue #20: [workers] when-rules; ci-fix and conflict default profiles

Blocked by: #13
Blocked by: #19
Plan: `docs/manager-plan.md` (T11)

**Scope**
`[workers.<label>]` may carry `when = "<natural-language description>"`. The manager prompt lists labels with their `when`; `ROUTE` and `FIX` are only accepted for listed labels. Ship two default profiles in `templates/factory.toml`: `ci-fix` (read the failing job log, fix or declare flake with evidence) and `conflict` (resolve the rebase, keep both intents, no semantic changes). New profiles remain host-config, human-applied.

**Touches**
`factory/config.py` (`[workers]` table-or-array), `factory/manage.py`, `factory/dashboard.py`, `factory/templates/factory.toml`.

**Exit gate**
A `ROUTE chore` decision on a repo without a `chore` worker is rejected and recorded; with one, the label is applied; a `FIX` on red CI runs the `ci-fix` argv. Existing array-form `[workers]` still loads. `uv run pytest tests/test_factory.py` passes.

**Out of scope**
The manager writing host config.

## Comment by @mikeroySoft (2026-09-05T00:50:47Z)

Triage: The issue is fully specified: it states a clear problem (add natural-language `when` rules to `[workers.<label>]`, list them in the manager prompt, restrict ROUTE/FIX to existing labels, and ship `ci-fix` and `conflict` default profiles), names the exact files to touch (config.py, manage.py, dashboard.py, factory.toml), and provides an exit gate with observable done-conditions (ROUTE chore without a worker is rejected and recorded, with a worker the label is applied, FIX on red CI runs the ci-fix argv, array-form `[workers]` still loads) plus a concrete verification command (`uv run pytest tests/test_factory.py`). No design judgment or policy/blast-radius concern is required; the human-applied note refers to runtime application, not implementation.

Agent brief: Add optional `when = "<natural-language>"` to `[workers.<label>]` entries. The manager prompt must list each label with its `when`, and ROUTE/FIX decisions are only accepted for labels that exist as workers. Add two default profiles to `templates/factory.toml`: `ci-fix` (reads the failing job log, fixes or declares a flake with evidence) and `conflict` (resolves the rebase, keeps both intents, no semantic changes); these remain host-config that humans apply. Preserve loading of the existing array-form `[workers]`. Verification: `uv run pytest tests/test_factory.py` passes; a ROUTE chore on a repo without a `chore` worker is rejected and recorded, with a `chore` worker the label is applied, and a FIX on a red CI run executes the `ci-fix` argv.

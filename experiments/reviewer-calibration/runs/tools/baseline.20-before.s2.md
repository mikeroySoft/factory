## Standards

Sources: README.md:85-107, docs/manager-plan.md §4.3–4.4 (no AGENTS.md/CONTRIBUTING.md in repo).

**Documented-standard violations: none found.**
- "record `manage` event before GitHub mutations" — `factory/manage.py:219` records before `apply()` at `:221`; inside FIX the only pre-mutation call is the read-only `gh pr view` at `:107-108`.
- "FIX does not merge or requeue the issue" (README:97-98) — FIX branch `return`s at `manage.py:131` before the shared `--remove-label ready-for-human --add-label ready-for-agent` at `:158-159`.
- "Only a fresh APPROVE restores `factory-approved`" (README:99-100) — `manage.py:121-128`: escalate on gate FAIL, `approve_pr` only on `verdict == "APPROVE"`.
- "neither ROUTE nor FIX accepts unlisted labels" — `manage.py:199` filters `RESERVED_LABELS`, `:213` passes the filtered dict to `parse`, `:64-66` rejects unknown/reserved FIX workers.

**Signature / return-shape mismatches: none found.** `worker_round(n, wt, labels, title, extra, attempt, deadline) -> (ok, report, logfile)` (`dispatch.py:915-923`) vs `manage.py:117-120`; `review(wt, n, gate_report)` (`dispatch.py:409`) vs `:125`; `escalate(n, reason, log_path)` (`dispatch.py:375`) vs `:122,130`; `pr_comment`/`approve_pr`/`gh_json` match. Host-layer `merge()` (`config.py:203-208`) recurses on dicts, so table-form `[defaults.workers.<label>]` entries compose before the normalization loop at `config.py:289-298`; `Config.worker()` (`config.py:135-138`) still sees `dict[str, list[str]]`.

**Baseline smells (judgement calls, non-blocking):**
- *Divergent Change* — `apply()` (`manage.py:100-159`) now mixes four `gh`-only label/body arms with one full worker→gate→push→review orchestration arm (`:104-131`).
- *Long conditional* — `manage.py:109` packs four unrelated preconditions (worktree, PR state, branch, review decision) into one `if`.

## Spec

Issue #20 / plan T11 (`docs/manager-plan.md:341-352`).

**Missing / partial: none blocking.**
- `when` parsed and layered: `config.py:289-298`; array form still loads (`tests/test_factory.py:147-165`).
- Manager prompt lists labels with `when`: `manage.py:200-201`; asserted at `tests/test_factory.py:673`.
- ROUTE `chore` without worker → recorded HUMAN, no `issue edit`; with worker → `--add-label chore`: `tests/test_factory.py:652-676`.
- FIX on red CI runs the `ci-fix` argv: `manage.py:117-118` → `cfg.worker({"ci-fix"}, …)` first-match (`config.py:137`); `fix-worker` stub execution asserted at `tests/test_factory.py:713-716`.
- Profiles shipped commented in `factory/templates/factory.toml:41-47` with copy-to-host instructions at `:37-39` — consistent with "New profiles remain host-config, human-applied" and the template's "defaults are shown commented out" convention (`:1-3`).
- Informational: `dashboard.py:847` exports `worker_when` but `dashboard.html:893-894` renders only `cfg.workers`; the issue names dashboard.py under Touches without a UI acceptance criterion, so not a REVISE item.
- Informational: T11's "the manager may recommend one in a HUMAN diagnosis" has no MENU text (`manage.py:26`); HUMAN is free text, so it's already permitted. Not in the issue's exit gate.

**Scope creep:** `manage.py:109` also refuses FIX when `reviewDecision == "CHANGES_REQUESTED"` — narrow trust guard consistent with §4.4, not unrelated behavior.

**Implemented but wrong: none found.**

Summary — Standards: 0 violations, 2 judgement-call smells (worst: `apply()` divergent change). Spec: 0 acceptance failures, 2 informational gaps (worst: `worker_when` not rendered in dashboard UI).

VERDICT: APPROVE

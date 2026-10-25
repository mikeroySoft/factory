**Standards** — SKILL.md/README conventions hold: `.factory/` stays the gitignored state dir for `manager-prompt-<n>.md` (`factory/manage.py:264`), doctor rows use the existing `report(status|None, label, detail)` shape (`factory/onboard.py:226-238`), CHANGELOG gets an `## Unreleased` entry, template + README updated in step. No CONTRIBUTING.md exists.

**Spec** (issue #62 "After this change" + brief):
- File transport: `manager_cmd(prompt_path, cwd)` expands a path (`factory/config.py:146-151`); manage writes `<factory>/manager-prompt-<n>.md` (`manage.py:264-266`); `learn.py:63-68` migrated to a tempfile so the only other caller doesn't break. ✔
- Bare `{prompt}` for omp → `ConfigError` naming `@{prompt}`; other programs untouched (`config.py:149-150`). Caught per-ticket in manage (`manage.py:279`) so a stale host config yields a HUMAN diagnosis instead of aborting the pass. ✔
- Doctor row: WARN unset, FAIL naming missing placeholder/executable, PASS otherwise (`onboard.py:226-238`); extra omp-bare check keeps doctor consistent with the runtime rejection. ✔
- Nonzero exit: `Manager command exited N (argv: <unexpanded json>)` + ≤5 stderr lines; `escalate` event with `reason: manager_failed` (`manage.py:268-274`). All `escalate` consumers traced: `dispatch.escalation_packet` round arithmetic (`dispatch.py:376`), `manage_pass` latest-escalation lookup (`manage.py:237-238`), `stats.human_touch` (`stats.py:167-174`; row dict spread into stats rows/dashboard via `collect_rows:281`); `learn.py:39` unaffected (ticket already finished). Human-takeover guard still runs after the failure branch. ✔
- Docs: README §Configuration + `templates/factory.toml` state file-for-worker/manager, text-for-reviewer. ✔
- "DEFAULT_MANAGER": no such constant exists in base (`manager=None` disables); the documented example commands moved to `@{prompt}`. Acceptable reading.

**Plan drift** — plan not recorded, so no named file list. Beyond the issue's "Touches": `CHANGELOG.md`, `factory/dispatch.py` (round exclusion), `factory/learn.py` (signature caller), `factory/stats.py` (counting), `onboard.py` instead of nonexistent `doctor.py`. Each is required by the new event or the signature change; none exposes a defect. Missing proof: the issue's manual District #26 kept-packet replay is not in the gate report; handoff only cites a prior attempt's log (unverified). Not a code defect.

```json
{"verdict":"APPROVE","findings":[
{"severity":"nit","pass":"bug","path":"factory/manage.py","line":273,"summary":"`manager_failed` escalate event is recorded before the post-run `human_activity` check (line 282), so a failure during a human takeover is counted in `manager_failures` though no diagnosis is posted; move the record after the check or accept the over-count."},
{"severity":"nit","pass":"spec","path":"factory/manage.py","line":274,"summary":"Recording `escalate` inside the manage lifecycle scope sets execution.outcome=project_escalation/reason=manager_failed (dispatch.py:245-248); `manager_failed` is absent from runtime_events.REASONS (runtime_events.py:34-44), so runtime readers drop the reason to None. Same lenient path as existing free-text escalate reasons; add it to REASONS if the runtime view should show it."}
]}
```
VERDICT: APPROVE

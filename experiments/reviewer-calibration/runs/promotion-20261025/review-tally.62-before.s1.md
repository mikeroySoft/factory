**Trace**

- Transport: `manage.py:263-265` writes `.factory/manager-prompt-<n>.md`, `config.py:146-151` expands the path and rejects bare `{prompt}` for `omp`; `learn.py:64-68` moved to a temp file. Matches issue bullets 1 and "existing host configs" bullet.
- Failure body: `manage.py:267-270` — `Manager command exited N (argv: <unexpanded>)` + last 5 stderr lines. Matches.
- Doctor: `onboard.py:145-157` — WARN unset / FAIL missing placeholder or executable / PASS. Matches.
- Docs: README §Configuration + template updated. Matches.
- Round arithmetic: `dispatch.py:375-378` excludes `manager_failed`; `manage.py:241-243` blocks replay via the `manage` event. Verified end to end.

**Defect**

`manage.py:272` records the `manager_failed` escalate event *before* the `human_activity` re-check at `manage.py:281`. On the next pass `manage.py:237` picks that new event as "the escalation", so `human_activity` (`manage.py:136`) only looks at timeline items after the failure timestamp. Trigger: manager exits nonzero while a human comments/relabels during the run (the race the code explicitly guards). Pre-diff: next pass sees the original escalate event → human's comment detected → skip. Post-diff: the comment predates the manager_failed `at`, no `manage` event was recorded (the `continue` fired), so the manager reruns and can apply RETRY/REWRITE/SPLIT on a human-owned ticket. Violates docs/manager-plan.md:174-175 ("If a human has commented or changed labels since the escalation, the manager skips the ticket"). Fix: record the event after the `human_activity` check, next to the `manage` record (`manage.py:286`).

**Plan drift / proof**

Plan not recorded. Beyond the issue's "Touches": `factory/dispatch.py` (round exclusion, requested in review round 1) and `factory/learn.py` (required by the `manager_cmd` signature change) — both justified. Template `[workers.ci-fix]`/`[workers.conflict]` example commands rewritten (`factory.toml:44,48`), dropping the profile-specific worker instructions; the originals were valid (inline text referencing the path, not bare `{prompt}`) — unrequested. The exit gate's manual District #26 replay is not in the gate report (handoff cites a prior attempt's log only).

```json
{"verdict":"REVISE","findings":[{"severity":"important","pass":"bug","path":"factory/manage.py","line":272,"summary":"manager_failed escalate event is recorded before the human_activity re-check; on the next pass it becomes the 'latest escalation' and resets the human-activity window, so a human comment/relabel made during a failed manager run is no longer detected and the manager reruns (and may apply RETRY/REWRITE/SPLIT) on a human-owned ticket — regression vs pre-diff behavior and docs/manager-plan.md:174. Record the event after the human_activity check, alongside the `manage` record."},{"severity":"nit","pass":"spec","path":"factory/templates/factory.toml","line":44,"summary":"ci-fix/conflict example commands were valid worker-shaped inline prompts; rewriting them to `@{prompt}` drops the profile-specific instructions (flake evidence, rebase restart) — unrequested change beyond the brief."},{"severity":"nit","pass":"bug","path":"factory/manage.py","line":272,"summary":"stats.human_touch counts every `escalate` event (stats.py:167) regardless of reason, so a manager failure now inflates escalation_count/escalations_per_week by one; the issue asked that stats can count it, but distinguishing reason was not done."}]}
```
VERDICT: REVISE

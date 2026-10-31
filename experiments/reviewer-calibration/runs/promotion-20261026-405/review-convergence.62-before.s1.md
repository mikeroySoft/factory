The diff covers most of issue #62. It needs one fix before approval: a failed manager run can now make a ticket look untouched even after a human took it over.

**Important (bug):** `factory/manage.py:272-273` records the new `escalate` event with `reason="manager_failed"` *before* the "has a human taken over?" check at `factory/manage.py:281`. If a human comments while a failing manager command is still running:
1. Line 281 sees the human activity and skips the ticket, so no `manage` event is recorded for that round.
2. On the next pass, `manage_pass` picks the `manager_failed` event as the newest escalation (`manage.py:237`). That round has no `manage` event, so the ticket is processed again.
3. `human_activity` only looks at timeline items after that event's timestamp, and the human's comment came earlier, so it is missed.
4. The manager runs again on a ticket a human owns. It can post a comment, or apply a RETRY/REWRITE that relabels the ticket.

Before this diff, the next pass still used the original escalation's timestamp and skipped the ticket. The fix is to move the record next to the `manage` record at `manage.py:286`, after the check.

**Spec coverage against the brief:**
- **Prompt as a file:** met. The manager prompt is written to `<factory>/manager-prompt-<n>.md`, and the `@<path>` test checks the file's contents.
- **Bare `{prompt}` for `omp`:** met. It raises a `ConfigError` that names `@{prompt}`.
- **Failure message:** met. A nonzero exit posts the one-line cause plus at most 5 stderr lines and records `reason: manager_failed`.
- **Doctor row:** met. It shows WARN when the manager is unset and FAIL for a missing placeholder or executable.
- **Docs:** the README and the init template are updated.

**Plan drift:** no plan was recorded, so I compared against the issue's "Touches" list.
- **Files not named there:**
  - `factory/dispatch.py`: the round-count fix asked for in the earlier review round.
  - `factory/learn.py`: needed because `manager_cmd` changed signature.
  - `factory/onboard.py`: the doctor code lives here; the issue named `doctor.py`, which doesn't exist.
  - `factory/templates/factory.toml`: this is the "init template" the issue names.

  None of these exposes a required defect.
- **Missing proof:** the gate report has no evidence for the manual exit-gate item, re-running `factory manage` on District #26's kept packet. The worker's handoff says only an earlier attempt did that replay.

```json
{"verdict":"REVISE","findings":[{"id":"F1","severity":"important","pass":"bug","class":"manager-failed-event-defeats-human-takeover-guard","path":"factory/manage.py","line":272,"summary":"The manager_failed escalate event is recorded before the human_activity recheck at manage.py:281. If a human acts while a failing manager runs, the ticket is skipped without a manage event. The next pass treats the new event as the escalation (manage.py:237), finds no manage event for the round, and checks human activity only after the new timestamp. It misses the human's earlier activity, so the manager reruns and can comment on or relabel a human-owned ticket. The pre-diff code did not have this regression. Fix: record manager_failed after the human_activity check, next to the manage record at manage.py:286.","instances":[{"path":"factory/manage.py","line":272,"summary":"dispatch.record('escalate', ..., reason='manager_failed') runs before the human_activity guard"}]},{"id":"F2","severity":"nit","pass":"spec","class":"misleading-rejection-docs-for-workers","path":"README.md","line":100,"summary":"The docs say bare `omp {prompt}` 'is rejected' for worker and manager commands, but only manager_cmd rejects it. Worker argv is never validated, so a bare {prompt} worker is not rejected by factory.","instances":[{"path":"README.md","line":100,"summary":"'passes the path as prompt text and is rejected' applies only to the manager"},{"path":"factory/templates/factory.toml","line":25,"summary":"The [workers] comment claims bare {prompt} is rejected"}]},{"id":"F3","severity":"nit","pass":"spec","class":"out-of-scope-template-profile-instructions-dropped","path":"factory/templates/factory.toml","line":44,"summary":"The ci-fix and conflict example worker commands lost their profile-specific instructions (read the failing CI log, flake needs evidence; preserve both intents, restart an aborted rebase) and became the plain default command. These were valid commands with inline text that referenced the path. The issue's out-of-scope note covers the manager only, and workers were not in scope.","instances":[{"path":"factory/templates/factory.toml","line":44,"summary":"ci-fix profile instructions removed"},{"path":"factory/templates/factory.toml","line":48,"summary":"conflict profile instructions removed"}]},{"id":"F4","severity":"nit","pass":"bug","class":"manager-failure-inflates-escalation-stats","path":"factory/manage.py","line":273,"summary":"stats.py:167 counts every escalate event without checking reason. Each manager failure therefore raises escalation_count and escalations_per_week even though no new ready-for-human escalation happened. dispatch.escalation_packet filters manager_failed, but stats does not.","instances":[{"path":"factory/manage.py","line":273,"summary":"An escalate event with reason manager_failed is counted as an escalation by stats"}]}]}
```
VERDICT: REVISE

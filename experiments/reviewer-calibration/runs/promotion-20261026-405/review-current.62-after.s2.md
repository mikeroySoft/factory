I found no blocking defects. The diff meets the issue's code and test acceptance criteria:

- **Prompt transport:** `Config.manager_cmd` now takes a prompt-file path, like the worker command.
- **Bare `{prompt}` for omp:** it is rejected with a `ConfigError` that names `@{prompt}`.
- **Doctor:** `factory doctor` has a new `manager command` row with WARN, FAIL and PASS states.
- **Failure message:** a nonzero exit posts `Manager command exited N (argv: …)` with the last 5 stderr lines.
- **Failure event:** it also records an `escalate` event with `reason: manager_failed`.
- **Counting:** stats, round numbering and manager eligibility all exclude those failure events.
- **Tests:** each code and test exit-gate criterion has a test.

The repo has no AGENTS.md or CONTRIBUTING.md. The README, template and CHANGELOG were updated to match the new behaviour.

**Plan drift:** no plan was recorded. Measured against the issue's Touches list, the diff also changes four files. Each is justified:
- `factory/dispatch.py` and `factory/stats.py`: needed so a manager failure is counted separately and does not use up a manager round.
- `factory/learn.py`: the only other caller of `manager_cmd`, so it had to follow the signature change.
- `CHANGELOG.md`: the changelog entry for this fix.

The issue names `factory/doctor.py`, but the doctor code lives in `factory/onboard.py`, which is where the diff changes it. The issue's manual exit gate (replaying District #26) has no proof in the gate report. The worker's handoff says only that an earlier attempt did it.

```json
{"verdict":"APPROVE","findings":[{"severity":"nit","pass":"bug","path":"factory/manage.py","line":273,"summary":"The manager_failed event is recorded with dispatch.record inside the per-ticket manage lifecycle scope, which sets that execution's outcome to `project_escalation` with reason `manager_failed`. README.md:372 defines `project_escalation` as 'not a broken runtime mechanism', but a nonzero manager exit is one; README.md:376 classes comparable nonzero worker/reviewer exits as `unknown`. Low impact: the old outcome here was `completed`, which was not accurate either."},{"severity":"nit","pass":"spec","path":"README.md","line":144,"summary":"The README says 'Manager command failures are counted separately as `manager_failures`', but only nonzero exits record that event. OSError and ConfigError failures (factory/manage.py:279-280, e.g. a missing executable or a bare omp `{prompt}`) post a HUMAN diagnosis but are not counted. The issue only requires counting nonzero exits, so the README wording overstates what is counted."},{"severity":"nit","pass":"spec","path":"factory/manage.py","line":264,"summary":"Plan drift: the issue's manual exit gate (re-run `factory manage` on District #26's kept packet and get a real DECISION line) has no proof in the gate report. The worker handoff only cites an earlier attempt's replay log."}]}
```
VERDICT: APPROVE

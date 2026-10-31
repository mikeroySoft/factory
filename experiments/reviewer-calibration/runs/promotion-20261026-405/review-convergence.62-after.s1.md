The diff meets every code-level acceptance criterion in issue #62. I found no Important defects, only three nits. I read the code and did not run any builds or tests.

**Spec (issue #62 and its agent brief):** every code-level criterion is covered:
- **Prompt as a file:** the manager's prompt goes in a file and is passed as a path. The file is written at `factory/manage.py:264-266` and the path filled in at `factory/config.py:146-151`. `factory learn` uses a temporary file instead (`factory/learn.py:64-68`).
- **Bare `{prompt}` rejected for `omp`:** a `ConfigError` names the fix (`factory/config.py:149-150`). `factory/manage.py:279` catches it, so a bad command produces a HUMAN diagnosis instead of crashing the pass.
- **Failure message:** a nonzero exit posts `Manager command exited N (argv: …)` with the placeholders unexpanded, plus the last 5 stderr lines. It also records an `escalate` event with `reason: manager_failed` (`factory/manage.py:268-274`).
- **Stats:** `manager_failures` is counted separately and kept out of the escalation count and the round numbering (`factory/stats.py:167-174`, `factory/dispatch.py:375-378`, `factory/manage.py:237-238`).
- **Doctor:** the `manager command` row reports WARN when unset, FAIL naming the missing placeholder or executable, and PASS otherwise (`factory/onboard.py:226-238`). WARN does not change doctor's exit code.
- **Docs:** `README.md:97-104,219-220` and `factory/templates/factory.toml:25,51,63-65` say `{prompt}` is a file for workers and the manager, and inline text for the reviewer only.
- **Tests:** the issue's test requirements are covered in `tests/test_factory.py`.
- **Default manager command:** there is no `DEFAULT_MANAGER` in the code because the manager is optional. The commented default in the template now uses `@{prompt}`.

**Standards:** there is no AGENTS.md or CONTRIBUTING.md in the snapshot. The diff follows the README and CHANGELOG conventions, and it adds no new abstractions.

**Plan drift:** no plan was recorded, so I compared against the issue's "Touches" list.
- **Changed but not named:**
  - `CHANGELOG.md`.
  - `factory/dispatch.py`: round numbering, a fix the reviewer asked for.
  - `factory/learn.py`: a caller of the changed `manager_cmd`.
  - `factory/stats.py`: needed for "so `factory stats` can count it".
  - `factory/onboard.py`: the issue names `factory/doctor.py`, which doesn't exist; doctor lives here.

  Each change follows from the spec or from a changed caller. None exposes a defect.
- **Missing proof:** the issue's manual check (re-run `factory manage` on District #26's kept packet and get a real DECISION line) isn't in the gate report. The worker's notes say this run did not repeat it and point to an earlier attempt. That is missing evidence, not a code defect.

```json
{"verdict":"APPROVE","findings":[{"id":"F1","severity":"nit","pass":"bug","class":"unbounded-stderr-line-length","path":"factory/manage.py","line":269,"summary":"The failure message keeps the last 5 stderr lines but does not limit their length. A single minified JS line can still be posted whole. If the comment goes over GitHub's 65536-character limit, the comment command fails, apply() is logged as mechanism_failure, and the ticket gets no diagnosis. The 'last 5 stderr lines' criterion itself is met.","instances":[{"path":"factory/manage.py","line":269,"summary":"tail = last 5 stderr lines with no character limit"}]},{"id":"F2","severity":"nit","pass":"bug","class":"execution-outcome-misclassified","path":"factory/manage.py","line":273,"summary":"Recording an 'escalate' event inside the manage scope triggers dispatch.record (factory/dispatch.py:245-248), which marks the manage run's outcome as project_escalation. README says project_escalation means 'not a broken runtime mechanism', but here the manager command itself failed.","instances":[{"path":"factory/manage.py","line":273,"summary":"dispatch.record('escalate', ..., reason='manager_failed') inside the lifecycle 'manage' scope"}]},{"id":"F3","severity":"nit","pass":"spec","class":"duplicated-omp-bare-prompt-check","path":"factory/onboard.py","line":234,"summary":"Doctor repeats the omp bare-{prompt} check from Config.manager_cmd (factory/config.py:149) with different wording, so the two can drift apart. Doctor could call cfg.manager_cmd and report the ConfigError instead.","instances":[{"path":"factory/onboard.py","line":234,"summary":"doctor's copy of the check"},{"path":"factory/config.py","line":149,"summary":"Config.manager_cmd's copy of the check"}]}]}
```
VERDICT: APPROVE

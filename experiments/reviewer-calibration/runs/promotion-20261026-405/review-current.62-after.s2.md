**Standards.** No AGENTS.md/CONTRIBUTING.md in the repo; README references CHANGELOG, and the diff adds an `Unreleased` entry (CHANGELOG.md:8-10). README/template updated per issue (README.md:38-47, templates/factory.toml:251-253).

**Spec (issue #62 + brief).**
- File transport: `manager_cmd(prompt_path, cwd)` expands a path (config.py:81-86); manage.py writes `<factory>/manager-prompt-<n>.md` (manage.py:149-151); `learn.py` migrated (learn.py:122-126). ✓
- Bare `{prompt}` for omp → `ConfigError` naming `@{prompt}` (config.py:84-85), other programs untouched. ✓ Test asserts `@<path>` readable content and the error (tests:265-280). ✓
- Nonzero exit → `Manager command exited N (argv: <unexpanded>)` + last 5 stderr lines (manage.py:154-158); `escalate` event with `reason: manager_failed` (manage.py:159-160); stats count it (stats.py:211-212, 220). ✓ Tests:353-391.
- Doctor row: WARN unset / FAIL missing placeholder or executable / PASS (onboard.py:180-192); tests:298-324. ✓
- Round arithmetic and manage selection exclude `manager_failed` (dispatch.py:97-101, manage.py:139-140) so the failure neither inflates escalation counts nor rounds; human takeover still checked after failure (manage.py:168-169; test:413-436). ✓
- `DEFAULT_MANAGER` named in the issue does not exist in the snapshot; the template default is the worker shape. ✓

**Plan drift.** No plan recorded. Files beyond the issue's "Touches": `CHANGELOG.md` (repo convention), `factory/dispatch.py` (round arithmetic; approved in round-1 review per issue comment), `factory/learn.py` (caller of the changed `manager_cmd` signature — required), `factory/stats.py` (issue: "so `factory stats` can count it"). The issue's manual District #26 replay is not in the gate report; handoff cites a prior-attempt replay log. Not a required defect.

```json
{"verdict":"APPROVE","findings":[
{"severity":"nit","pass":"spec","path":"factory/onboard.py","line":188,"summary":"Duplicates the omp bare-{prompt} rule already enforced in config.manager_cmd (config.py:84). Doctor could call cfg.manager_cmd(...) in a try/except ConfigError for the omp case so the two copies can't drift; placeholder/PATH checks would stay in doctor."},
{"severity":"nit","pass":"spec","path":"factory/manage.py","line":166,"summary":"OSError/ConfigError path (missing executable, bad omp config) posts HUMAN but records no reason=manager_failed event, so README.md:55 'Manager command failures are counted separately' undercounts; issue only mandates the nonzero-exit case."},
{"severity":"nit","pass":"spec","path":"factory/config.py","line":81,"summary":"Brief says Config.manager_cmd writes <factory>/manager-prompt-<n>.md; diff has callers write it (manage.py:149-150, learn.py:123-125) and manager_cmd only expands. Behaviorally equivalent and avoids threading n; noting the deviation."}
]}
```
VERDICT: APPROVE

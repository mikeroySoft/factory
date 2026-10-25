Review artifacts `./review.diff` and `./snapshot` were absent from the sandbox (reported via `xd://report_issue`); the cwd is the gated checkout itself (HEAD `68b5d16`, base `origin/main` = `f6dfc77`), so I reviewed `git diff origin/main..HEAD` against issue #62 fetched from github.com.

## Standards
Repo has no AGENTS.md/CONTRIBUTING.md; conventions come from README/CHANGELOG. Diff adds an `## Unreleased` CHANGELOG entry (CHANGELOG.md:4), updates README §Configuration and the `factory init` template consistently (`{prompt}` = file for workers+manager, inline text for reviewer; README.md:97-101, templates/factory.toml:63-65), and commits carry `Signed-off-by`. `manager_cmd` mirrors `Config.worker`'s path-expansion shape (config.py:146-151 vs 138-141). No violations.

## Spec (issue #62 + agent brief)
- `manager_cmd(prompt_path, cwd)` expands `{prompt}` with a path; rejects bare `{prompt}` when argv[0] is `omp` with a `ConfigError` naming `@{prompt}` (config.py:149-150). Other programs untouched. ✓ Test: tests/test_factory.py:150-165.
- Prompt written to `<factory>/manager-prompt-<n>.md` (manage.py:264-265). ✓ Test with 200 KB prompt via District-shaped command: test_factory.py:730-749.
- Nonzero exit → `Manager command exited N (argv: <unexpanded>)` + last 5 stderr lines; `escalate` event with `reason: manager_failed` (manage.py:267-274). ✓ Test: test_factory.py:751-786. `ConfigError` contained per ticket (manage.py:279). ✓
- doctor `manager command` row: WARN unset / FAIL missing placeholder or executable / PASS (onboard.py:226-238). ✓ Test matrix: test_factory.py:410-436. Issue's `[dispatch.manager]`/`doctor.py` names don't exist in this repo; `[manager]`/`onboard.py` are the real locations.
- `DEFAULT_MANAGER` doesn't exist (manager default is unset = disabled); README/template defaults now show `@{prompt}`. Acceptable reading.
- `learn.py` caller migrated to a temp prompt file (learn.py:64-68). ✓

## Plan drift
Plan not recorded; issue "Touches" names config.py, manage.py, doctor.py, template+README, tests. Not named but changed: `CHANGELOG.md`, `factory/dispatch.py` (round arithmetic excludes `manager_failed`), `factory/learn.py` (required caller of changed signature), `factory/stats.py` (`manager_failures` column). dispatch/stats changes are the reviewer-required fixes recorded in the issue's handoff comment and serve "so `factory stats` can count it". None exposes a defect.
Planned proof missing from gate report: the manual District #26 kept-packet replay producing a real `DECISION:` line. Handoff says a prior attempt did it (`.factory/district-26-replay.log`); not reproduced for this revision and not in the gate. Transport is covered by the 200 KB prompt test, so not blocking.

```json
{"verdict":"APPROVE","findings":[{"severity":"nit","pass":"bug","path":"factory/manage.py","line":269,"summary":"stderr tail is bounded by line count only; one long minified-JS line (the issue's own trigger) can push the HUMAN comment past GitHub's 65536-char body cap, so `apply` fails after the `manage` event is already recorded (manage.py:287-295) and the ticket gets no diagnosis and no retry. Consider also capping characters."},{"severity":"nit","pass":"bug","path":"factory/manage.py","line":273,"summary":"`dispatch.record(\"escalate\", reason=\"manager_failed\")` runs inside the `manage` lifecycle scope, so dispatch.record (dispatch.py:245-248) sets that execution's outcome to `project_escalation`; runtime_events._reason drops `manager_failed` (not in REASONS), leaving the manage execution as an escalation with no reason in the runtime feed. Pre-existing allowlist pattern; add the reason to REASONS if the feed should show it."}]}
```
VERDICT: APPROVE

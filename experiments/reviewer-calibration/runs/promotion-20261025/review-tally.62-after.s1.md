**Inputs.** `./review.diff` and `./snapshot` absent from cwd; used `git diff origin/main...HEAD` (f6dfc77..68b5d16, 6 commits, 10 files) against the checked-out revision. Line cites are post-diff file lines in that checkout. Issue fetched via GitHub API.

## Standards
No AGENTS.md/CONTRIBUTING.md in repo; conventions are README + CI (`python -m unittest discover -s tests`, CHANGELOG under README:50). Diff adds the CHANGELOG entry (CHANGELOG.md:8-10), keeps tests in the existing `tests/test_factory.py` stub-bin pattern, `.factory/` prompt file sits inside the gitignored state dir (.gitignore:8). No documented-rule breach. Smell baseline: the `Path(argv[0]).name == "omp" and "{prompt}" in argv` condition appears in both `config.py:84` and `onboard.py:188` (duplicated one-liner; judgement call, not worth an abstraction).

## Spec (issue #62)
- `manager_cmd(prompt_path, cwd)` expands `{prompt}` with a path; bare `{prompt}` for omp → `ConfigError` naming `@{prompt}` (config.py:81-86); test asserts `@<path>` readable with prompt text and the error (tests/test_factory.py:150-166). Caller writes `<factory>/manager-prompt-<n>.md` (manage.py:264-265) — split of "write" between caller and `manager_cmd` is equivalent to the criterion.
- No `DEFAULT_MANAGER` exists in `config.py` (manager default is `None`, config.py:105); the template's documented default is now the worker shape (templates/factory.toml:251-253) and README documents file-for-worker/manager, text-for-reviewer (README.md:38-42, 65-66).
- Nonzero exit: body `Manager command exited N (argv: <unexpanded json>)` + last 5 stderr lines (manage.py:268-271); `escalate` event with `reason=manager_failed` (manage.py:273-274); `stats` counts it (stats.py:167-174, 303). Test asserts prefix and exactly 5 tail lines (tests/test_factory.py:369-372).
- Doctor `manager command` row: WARN unset, FAIL per missing placeholder/executable, PASS otherwise (onboard.py:180-192); `report(None, …)` maps to WARN (onboard.py:168). Table-driven test covers all cases incl. text output (tests/test_factory.py:298-324).
- Round-1 approved changes present: `ConfigError` contained per ticket (manage.py:279), `manager_failed` excluded from round arithmetic (dispatch.py:375-378, manage.py:237-238), human-takeover anchor preserved because the original escalation stays the anchor and `human_activity` is re-checked post-run (manage.py:247, 282).
- Out-of-scope items untouched (decision menu, packet format, model).

## Plan drift
Plan not recorded; compared to the issue's "Touches". Unnamed files: `CHANGELOG.md` (repo convention), `factory/dispatch.py` (approved in review round 1 per issue comment), `factory/learn.py` (forced by `manager_cmd` signature change; second caller migrated, learn.py:64-68), `factory/stats.py` (serves "so `factory stats` can count it"; adds a `manager_failures` column — modest scope widening, no defect). Planned proof not in gate report: the manual District #26 kept-packet replay; the handoff says a prior attempt did it, this revision did not. Gate covers the unit-test exit gate; no required defect exposed.

```json
{"verdict":"APPROVE","findings":[]}
```
VERDICT: APPROVE

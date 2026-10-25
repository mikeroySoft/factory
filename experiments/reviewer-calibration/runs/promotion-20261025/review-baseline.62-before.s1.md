## Evidence basis

`./review.diff` and `./snapshot` were absent from the working directory. Reviewed `git diff origin/main...HEAD` (merge-base `f6dfc77` → `cfbdb6f`, 4 commits whose messages match the issue‑62 round‑1 handoff) in `/tmp/cal-tools-62-before-r9aijbfj/repo`. Issue #62 and its comments fetched via GitHub API. Line numbers are post‑change file lines.

## Standards

Repo has no AGENTS.md/CONTRIBUTING.md; README and the `factory init` template are the documented contract.

**Required**

1. **Docs claim a worker validation that does not exist.** `factory/templates/factory.toml:25` (under `[workers]`): "bare `{prompt}` passes path text and is rejected." `README.md:97-100` makes the same claim for "Worker and manager commands". `Config.worker` (`factory/config.py:138-141`) performs no such check; only `manager_cmd` (`config.py:149-150`) rejects. Trigger: operator writes `default = ["omp","-p","--cwd","{cwd}","{prompt}"]` trusting the template; nothing rejects it, omp receives the path as prompt text, the attempt burns silently. Impact: documentation deliverable required by AC "README.md §Configuration and the `factory init` template document `{prompt} = file` for workers and manager" states false behavior; the issue also scopes rejection to `manager_cmd` only ("Other programs are not second‑guessed"). Fix: scope the "is rejected" wording to the manager command (or drop the rejection clause from the worker lines). Adding a worker‑side guard is not requested.

**Optional (judgement calls)**

- Duplicated Code: the omp bare‑`{prompt}` test appears in `factory/config.py:149` and `factory/onboard.py:234` with different messages. One predicate/message shared by both would keep doctor and runtime in lockstep; not blocking.
- `factory/templates/factory.toml:44,48`: the `ci-fix`/`conflict` example commands were replaced with generic `@{prompt}`, erasing the profile‑specific instructions they carried ("Read the failing CI job log…", "Resolve the rebase conflicts…"). The old form was valid for omp (inline text embedding the path; the template's own line 30 documents that pattern) and not asked for by #62. Consider restoring.
- `factory/learn.py:68`: `ConfigError` from `manager_cmd` is uncaught in `factory learn`, so an old bare‑`{prompt}` omp config tracebacks there. Consistent with how `config.load` errors already surface at top level (`factory/cli.py:45`), and `learn` is outside #62's named touches; noted only.

## Spec (issue #62 + agent brief)

- `manager_cmd(prompt_path, cwd)` expands `{prompt}` with a path; prompt written to `<factory>/manager-prompt-<n>.md` by the caller (`factory/manage.py:263-265`), mirroring `cfg.worker` where `dispatch` writes `.factory-prompt.md`. Test `tests/test_factory.py:150-165` asserts `@<path>` readable with prompt content and `ConfigError` matching `@{prompt}` for bare `{prompt}` with omp. ✓
- Bare `{prompt}` rejected only when `argv[0]` basename is `omp` (`config.py:149`); other programs untouched. ✓
- Nonzero exit: `factory/manage.py:267-273` posts `Manager command exited N (argv: <json unexpanded argv>)` + last 5 stderr lines, records `escalate` with `reason="manager_failed"`. Test `tests/test_factory.py:752-779` asserts prefix, unexpanded placeholders, exactly `error-15..19`. ✓
- Doctor row `manager command`: WARN when unset, FAIL naming missing placeholder/executable (and omp bare `{prompt}`), PASS otherwise (`factory/onboard.py:226-238`); table test covers all branches incl. text output (`tests/test_factory.py:410-436`). ✓ (Issue named `factory/doctor.py`; doctor lives in `onboard.py` — no such file exists.)
- README §Configuration and template updated; README example gains `[manager]` with `@{prompt}` (`README.md:215-216`). ✓ except the overclaim in Required #1.
- "Default manager command becomes the worker shape": there is no `DEFAULT_MANAGER` in `config.py` (manager is `None` by default); the template/README example commands were updated instead. Acceptable reading.
- Round‑1 reviewer fixes (approved scope): `ConfigError` contained per ticket as HUMAN without replay (`manage.py:278-279`; test `:782-795`), `manager_failed` excluded from round arithmetic (`factory/dispatch.py:375-378`; test asserts round 2 then RETRY). ✓
- Side effect worth knowing, not a defect: `dispatch.record("escalate", …)` inside the `manage` lifecycle scope sets that execution's outcome to `project_escalation`/`manager_failed` (`dispatch.py:245-248`), and `stats.py:167` counts the event in `escalation_count` — the issue explicitly asks for stats visibility.
- Out of scope respected: decision semantics, packet format, model unchanged.

## Plan drift

No worker plan recorded. Against the issue's "Touches" list, files changed but not named: `factory/dispatch.py` (round arithmetic — required by round‑1 review) and `factory/learn.py` (second caller of the changed `manager_cmd` signature — required for the cutover). Both justified; neither exposes a defect.

Planned proof missing from the gate report: the manual exit‑gate item (re‑run `factory manage` on District #26's kept packet with the host command and get a real `DECISION:` line). The handoff says a prior attempt did this and this revision did not repeat it; the gate report covers only conflict‑markers/test/leak‑scan. Not blocking on its own.

## Verdict rationale

One required fix: the template/README assert a worker‑side rejection that the code does not implement (`factory/templates/factory.toml:25`, `README.md:97-100` vs `factory/config.py:138-141`). Everything else meets the acceptance criteria.

VERDICT: REVISE

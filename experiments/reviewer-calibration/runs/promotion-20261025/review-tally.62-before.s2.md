Inputs note: `./review.diff` and `./snapshot` were absent from the sandbox (reported via `xd://report_issue`); reviewed `git diff f6dfc77..cfbdb6f` in the checkout (HEAD = `cfbdb6f`, the handoff's gated commits). Issue #62 fetched from GitHub.

## Standards
No `AGENTS.md`/`CONTRIBUTING.md` in this repo; `CHANGELOG.md` is written per release (no Unreleased section), so no entry is owed. Commits carry `Signed-off-by`. `.factory/` is gitignored, so `manager-prompt-<n>.md` does not leak into the tree. No documented-rule violations.

## Spec (issue #62 acceptance)
- `manager_cmd(prompt_path, cwd)` expands `{prompt}` with a path; `manage.py` writes `<factory>/manager-prompt-<n>.md` (`factory/manage.py:263-265`); e2e test verifies `@<path>` + file content (`tests/test_factory.py:278-297`). ✔
- Bare `{prompt}` for `omp` → `ConfigError` naming `@{prompt}`; other argv[0] not second-guessed (`factory/config.py:59-60`). ✔ `manage.py:279` catches it per ticket (round-1 reviewer fix). ✔
- Doctor row `manager command` PASS/FAIL/WARN with placeholder/executable naming (`factory/onboard.py:145-157`; `report(None)` → WARN). Lives in `onboard.py`, not a new `doctor.py` — correct, that's where `doctor()` already is. ✔
- Nonzero exit → `Manager command exited N (argv: …unexpanded…)` + last 5 stderr lines, `escalate` event `reason=manager_failed` (`factory/manage.py:267-273`); test asserts exactly 5 lines and the event. ✔
- README §Configuration + `factory init` template document file-vs-text per role. ✔
- No `DEFAULT_MANAGER` constant exists in `config.py`; manager is opt-in, so "default becomes worker shape" is satisfied via README/template examples.

## Plan drift
Plan not recorded. Beyond the issue's "Touches": `factory/dispatch.py` (excludes `manager_failed` from round arithmetic — required so a failed manager doesn't burn a round) and `factory/learn.py` (caller of the changed `manager_cmd` signature — required migration). Gate report lacks the issue's manual District #26 replay; handoff claims it ran in a prior attempt. Not a defect.

```json
{"verdict":"APPROVE","findings":[{"severity":"nit","pass":"bug","path":"factory/manage.py","line":272,"summary":"The new `escalate` event with `reason=manager_failed` is counted by `stats.py:167` (`escalation_count = max(len(starts), len(escalated))`), so one real escalation followed by a manager crash reports 2 escalations for the ticket. dispatch.py filters it from round arithmetic but stats does not; filter there too if the count should mean human escalations."},{"severity":"nit","pass":"spec","path":"factory/templates/factory.toml","line":44,"summary":"Unrequested: the `ci-fix`/`conflict` worker examples dropped their role-specific inline instructions (which were valid omp usage — inline text telling the agent to read `{prompt}`), replaced by plain `@{prompt}` (also line 48). The brief only asked to document `{prompt}` as a file for workers/manager; the guidance now lives only in `when`."}]}
```
VERDICT: APPROVE

## Review — issue #62 (base `origin/main`..`68b5d16`)

Inputs: `./review.diff` and `./snapshot` were absent in this sandbox; diff regenerated as `git diff origin/main...HEAD` (10 files, +218/−33), identical range to the gate's `agent/62` series. Issue body + both comments (triage brief, round‑1 handoff) read from the GitHub API.

### 1. Standards

Repo has no `AGENTS.md`/`CONTRIBUTING.md`; documented rules found: dependency‑free Python package (`docs/c1-read-interface-handoff.md:104`), `CHANGELOG.md` kept per release (`README.md:50`), conventional `type(scope):` subjects with `Signed-off-by` on prior commits.

- Only stdlib imports added (`factory/learn.py:16` `NamedTemporaryFile`) — compliant.
- `CHANGELOG.md:4-6` adds an `Unreleased` entry — compliant.
- All six commits carry `Signed-off-by` and `fix(manager):`/`docs(manager):` subjects — matches base history.
- Smell (judgement, non‑blocking): the omp bare‑`{prompt}` rule is written twice — `factory/config.py:149` and `factory/onboard.py:234`. Two lines; not worth an abstraction. Noted only so the two don't drift.

No hard standards violations.

### 2. Spec (issue #62 + triage brief)

| Criterion | Evidence | Status |
|---|---|---|
| `manager_cmd(prompt_path, cwd)` expands `{prompt}` with a file path; prompt written to `<factory>/manager-prompt-<n>.md` | `factory/config.py:146-151`; `factory/manage.py:264-266` | ✓ |
| Bare `{prompt}` for `omp` → `ConfigError` naming `@{prompt}`; other programs not second‑guessed | `factory/config.py:149-150` (`Path(argv[0]).name == "omp"`, exact element match) | ✓ |
| Default/template manager command is worker shape | `factory/templates/factory.toml:63-65`; `README.md:219-220` | ✓ |
| README §Configuration + template document file for workers/manager, text for reviewer | `README.md:97-104`; `factory/templates/factory.toml:25,51,63-64` | ✓ |
| Doctor `manager command` row: PASS/FAIL (missing placeholder or executable)/WARN (unset) | `factory/onboard.py:226-237`; `report(None, …)` → WARN per `onboard.py:168` | ✓ |
| Nonzero exit posts `Manager command exited N (argv: …unexpanded…)` + last 5 stderr lines | `factory/manage.py:268-271` (`json.dumps(cfg.manager)` is the unexpanded template) | ✓ |
| Records `escalate` event with `reason: manager_failed`; `factory stats` can count it | `factory/manage.py:273-274`; `factory/stats.py:173-174`, `:303` | ✓ |
| Exit‑gate tests: `@<path>` readable content; `ConfigError` on bare `{prompt}`; HUMAN body prefix + ≤5 stderr lines; doctor WARN/FAIL | `tests/test_factory.py:150-165`, `:410-436`, `:730-751`, `:753-787` | ✓ |
| Manual District #26 replay | Not in gate report; round‑1 handoff cites a prior attempt's log. Unverifiable here. | — (manual item) |

Follow‑through I checked for correctness of the synthetic `escalate` event, since it is the one place the diff could regress sibling consumers:
- Round arithmetic excludes it — `factory/dispatch.py:375-378`.
- `manage_pass` anchors to the real escalation, so the failed round is not replayed and the human‑takeover anchor survives — `factory/manage.py:237-245`, test `tests/test_factory.py:835-858`.
- Stats escalation count/times exclude it — `factory/stats.py:167-172`.
- `learn.py:39` treats any `escalate` as "finished"; a `manager_failed` row only ever follows a real escalation, so no change in membership.
- No other `"escalate"` consumers in `factory/` (dashboard keys off labels).

Out‑of‑scope items (decision semantics, packet format, model) untouched.

### 3. Plan drift

Plan status: not recorded — no worker plan to diff against. Against the issue's **Touches** list (`config.py`, `manage.py`, doctor, template, README, tests), the diff additionally changes:
- `factory/learn.py:61-68` — required by the `manager_cmd` signature change (clean cutover of the only other caller).
- `factory/dispatch.py:375-378`, `factory/stats.py:164-174,303` — required so the new `manager_failed` event doesn't inflate rounds/escalation counts (would be a concrete defect without them; reviewer round 1 asked for it).
- `CHANGELOG.md:3-6` — repo convention.

None exposes a defect. Gate report lacks the issue's manual District #26 replay; that is not something the deterministic gate can produce.

### Required fixes

None.

### Optional suggestions

- `factory/runtime_events.py:34-44` — `manager_failed` is not in `REASONS`, so `--runtime-json` normalizes the manage execution's exit reason to `null` (`runtime_events.py:73-75`) while `events.jsonl` keeps it. Add it if runtime‑JSON consumers should see the reason; stats already works from the journal, so not required.
- `factory/manage.py:268` — the posted comment includes the full unexpanded manager argv as configured on the host. The issue asked for this, but any host‑only argv values (e.g. an explicit key flag) would land in a GitHub comment. Worth noting in README if that matters for public repos.
- `factory/config.py:149` / `factory/onboard.py:234` duplicate the omp check; keep in sync.

VERDICT: APPROVE

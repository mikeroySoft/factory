# Review: #62 — manager prompt-file transport

## Spec coverage (issue #62 "After this change" + Exit gate)

| Criterion | Evidence | Status |
|---|---|---|
| `manager_cmd(prompt_path, cwd)` expands `{prompt}` with a path; prompt written to `<factory>/manager-prompt-<n>.md` | `factory/config.py:146-151`; `factory/manage.py:264-266` | Met |
| Bare `{prompt}` for `omp` → `ConfigError` naming `@{prompt}`; other programs untouched | `factory/config.py:149-150` (exact element match, `Path(argv[0]).name == "omp"`) | Met |
| README §Configuration + `factory init` template document file-for-worker/manager, text-for-reviewer | `README.md:97-101`, `README.md:219-220`; `factory/templates/factory.toml:25`, `:51`, `:63-65` | Met |
| Doctor `manager command` row: WARN unset / FAIL missing placeholder or executable / PASS | `factory/onboard.py:226-238` | Met |
| Nonzero exit → `Manager command exited N (argv: <unexpanded>)` + last 5 stderr lines | `factory/manage.py:268-272` (`json.dumps(cfg.manager)` is the unexpanded template; `splitlines()[-5:]`) | Met |
| `escalate` event with `reason: manager_failed` countable by `factory stats` | `factory/manage.py:274-275`; `factory/stats.py:167-174`, `:303` | Met |
| Existing `learn` manager adapter migrated to the file transport | `factory/learn.py:64-68` (`NamedTemporaryFile` kept open across `dispatch.run`, so the path is readable by the child) | Met |
| Exit-gate tests: `@<path>` readable content; `ConfigError` on bare `{prompt}`; HUMAN body starts with `Manager command exited 1`, exactly 5 stderr lines; doctor WARN/FAIL rows | `tests/test_factory.py:150-165`, `:410-436`, `:752-787`, `:730-750` | Met |
| Reviewer round-1 required fixes (ConfigError contained per ticket; `manager_failed` excluded from round arithmetic and latest-escalation lookup) | `factory/manage.py:237-238`, `:279`; `factory/dispatch.py:375-377`; tests `:790-803`, `:835-858` | Met |

Consistency check on the new event: every reader of `escalate` events visible in the diff (`manage_pass` lookup, `escalation_packet` round count, `stats.human_touch`) filters `reason == "manager_failed"`, so a failed manager run neither re-triggers itself on the next pass nor consumes a round. `CHANGELOG.md:4-6` and `README.md:144-146` document the stats semantics.

## Standards

Only README.md exists as documented convention at this head; no AGENTS.md/CONTRIBUTING.md. No net-new abstractions introduced. Architecture rule "`config.py` as the single source of every repo-specific value" (README §Architecture) is respected: the omp-shape rule lives in `config.manager_cmd`, and doctor mirrors it rather than inventing a different rule.

## Required fixes

None.

## Optional suggestions

1. `factory/manage.py:264-266` — the prompt file is written before `cfg.manager_cmd` can raise `ConfigError` (`config.py:149-150`), leaving a stale `manager-prompt-<n>.md` on the misconfigured path. Harmless (gitignored `.factory/`, overwritten next run); calling `manager_cmd` first would avoid the write. Preference, not a rule.
2. `factory/onboard.py:233-234` duplicates the omp bare-`{prompt}` predicate from `config.py:149`. Two copies of one rule can drift; a shared helper in `config.py` would be the single source. Not required — both are one line and tested.
3. The issue's "Touches" names `DEFAULT_MANAGER` in `factory/config.py`; the diff changes only the template default (`factory/templates/factory.toml:65`). If a `DEFAULT_MANAGER` constant with the inline shape exists outside this diff, `manager_cmd` would now reject it with `ConfigError`. Not citable from the diff (and README states an unset command disables the manager, implying no code default); worth a one-line confirmation by the author.
4. `factory/manage.py:269-270` drops the old `proc.stdout` fallback when stderr is empty. Matches the issue text ("last 5 stderr lines"); a manager that reports failure on stdout will post only the one-line cause. Acceptable per spec.

VERDICT: APPROVE

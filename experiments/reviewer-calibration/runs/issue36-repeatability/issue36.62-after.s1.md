# Review: #62 — manager prompt transport (head `68b5d16`)

## Spec coverage

| Acceptance criterion | Evidence | Status |
|---|---|---|
| `manager_cmd(prompt_path, cwd)` expands `{prompt}` with a path; prompt written to `<factory>/manager-prompt-<n>.md` | `factory/config.py:146-151`; `factory/manage.py:264-266` | Met (write happens in `manage.py`, not inside `manager_cmd`; see Optional 1) |
| Bare `{prompt}` for `omp` → `ConfigError` naming `@{prompt}`; other programs untouched | `factory/config.py:149-150` (`Path(argv[0]).name == "omp"`, exact-element match) | Met |
| Default/template manager command is worker shape | `factory/templates/factory.toml:63-65`, `README.md:219-220` | Met for documented examples; see Optional 2 |
| README + template: `{prompt}` = file for workers and manager, text for reviewer | `README.md:34-38`, `README.md:97-106`, `factory/templates/factory.toml:25,51,63-64` | Met |
| `factory doctor` `manager command` row: PASS / FAIL (placeholder or executable named) / WARN when unset | `factory/onboard.py:226-238` | Met |
| Nonzero exit → `Manager command exited N (argv: …unexpanded…)` + last 5 stderr lines; `escalate` event with `reason: manager_failed` | `factory/manage.py:267-274` (`json.dumps(cfg.manager)` is the unexpanded argv; `splitlines()[-5:]`) | Met |
| Exit-gate tests: `@<path>` readable with prompt content; ConfigError regex; HUMAN body prefix + ≤5 lines; doctor WARN/FAIL | `tests/test_factory.py:150-165`, `:410-436`, `:730-751`, `:753-795` | Met |
| Out of scope (decision semantics, packet format, model) untouched | no diff hunks in `parse`/packet builder | Met |

Round-1 reviewer fixes carried in this head: `ConfigError` contained per ticket (`factory/manage.py:279`), `manager_failed` excluded from round arithmetic (`factory/dispatch.py:376-377`, `factory/manage.py:237-238`) and from escalation counts (`factory/stats.py:167-168`). These follow from the mandated `escalate`-shaped event: without the exclusion a manager failure would consume a manager round and inflate escalation stats, so they are correctness fixes, not new abstractions.

## Required fixes

None.

## Optional suggestions

1. `factory/config.py:146-151` — the issue text says `manager_cmd` "writes the prompt"; the diff instead has callers write (`factory/manage.py:264-265`, `factory/learn.py:64-68`). Behavior and the exit-gate test are satisfied either way, and the split lets `learn` use a temp file. No change needed; noting the wording divergence only.
2. Issue "Touches" names `DEFAULT_MANAGER` in `config.py`; the diff does not modify any such constant. [INFERENCE] If a `DEFAULT_MANAGER` with a bare `{prompt}` for `omp` exists outside the diff and is ever used as `cfg.manager`, it would now raise `ConfigError` at first escalation. The gate's passing tests and README "Disabled unless `manager.command` is configured" suggest the manager defaults to unset, so this is likely moot — worth a one-line confirmation in the handoff.
3. `factory/manage.py:272-273` — `manager_failed` rows reuse `event: "escalate"`. Three consumers now filter on `reason` (`dispatch.py:376`, `manage.py:237`, `stats.py:167`). [INFERENCE] Any other `escalate` consumer not in this diff (dashboard escalation lists, `learn`'s "escalation reasons" evidence) will treat a manager failure as an escalation. The issue mandated this event shape, so this is a documentation/awareness note, not a defect in the diff.
4. `factory/stats.py:303` — new `manager failures` table column widens every `factory stats` run even when the count is zero for all rows. The issue asked only that stats "can count it"; the JSON field alone satisfies that. Keep or drop per preference.

VERDICT: APPROVE

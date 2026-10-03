# Review: #62 — manager prompt transport

## Spec coverage (issue "After this change" / exit gate)

| Criterion | Evidence | Status |
|---|---|---|
| `manager_cmd(prompt_path, cwd)` expands `{prompt}` with a path; prompt written to `<factory>/manager-prompt-<n>.md` | `factory/config.py:146-151`, `factory/manage.py:264-266` | Met |
| Bare `{prompt}` for `omp` → `ConfigError` naming `@{prompt}`; other programs not second-guessed | `factory/config.py:149-150` (exact-element match, `Path(argv[0]).name == "omp"`) | Met |
| Nonzero exit posts `Manager command exited N (argv: …unexpanded…)` + ≤5 stderr lines | `factory/manage.py:268-272` (`json.dumps(cfg.manager)`, `splitlines()[-5:]`) | Met |
| Records `escalate` event with `reason: manager_failed`, countable by stats | `factory/manage.py:274-275`, `factory/stats.py:172-173` | Met |
| Doctor row: WARN unset / FAIL missing placeholder or executable / PASS | `factory/onboard.py:226-238` | Met |
| README §Configuration + `factory init` template document file vs text | `README.md:97-106,219-220`, `factory/templates/factory.toml:25,51,63-65` | Met |
| Tests: `@<path>` readable content; ConfigError; HUMAN body prefix and 5-line cap; doctor WARN/FAIL | `tests/test_factory.py:150-165,410-436,755-787` | Met |
| Round-1 reviewer fixes: ConfigError contained per ticket; `manager_failed` excluded from round arithmetic | `factory/manage.py:278`, `factory/dispatch.py:376-377`, `factory/manage.py:237-238`; tests `tests/test_factory.py:789-802` | Met |

Round-number exclusion and human-takeover ordering (`manage.py:237-238`, `dispatch.py:376`) are consistent: the `manager_failed` record carries the real escalation's `round` and `packet`, so the next genuine escalation gets round 2 and `human_activity(n, escalation)` still keys off the original escalation.

## Required fixes

None.

## Optional suggestions

1. `factory/learn.py:64-68` — `cfg.manager_cmd(...)` can now raise `ConfigError` (bare `{prompt}` for omp). `manage.py:278` catches it per ticket; `learn` does not. [INFERENCE] Depending on the CLI's top-level handling this surfaces as a traceback rather than a one-line config error. Non-blocking: not an acceptance criterion, and the message itself names the fix.
2. Issue "Touches" names `DEFAULT_MANAGER` in `factory/config.py`; the diff does not touch any such constant. If one exists with the inline shape, `config.py:149` would reject it at every manage pass. [INFERENCE] README:99 ("Disabled unless `manager.command` is configured") suggests no effective default, so likely moot — worth a one-line check on the target host.
3. `factory/manage.py:264-265` leaves `manager-prompt-<n>.md` in `.factory/` after the run. Matches the worker's kept-artifact behavior and is useful forensics; mention only because `learn.py` chose the opposite (temp file) — two conventions for the same transport.

No net-new abstractions introduced; the `manager_failures` stats column (`stats.py:173-174,303`) is the minimum needed for the "so `factory stats` can count it" criterion.

VERDICT: APPROVE

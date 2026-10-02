## Standards (README conventions)

No violations. README §Configuration and the `factory init` template now document `{prompt}` = file for workers and manager, inline text for the reviewer only (`README.md:97-105`, `README.md:219-220`, `factory/templates/factory.toml:63-65`). CHANGELOG entry present (`CHANGELOG.md:4-6`). Lifecycle/event contract unchanged: the new `manager_failed` row is a legacy `escalate` event with an added `reason`, which the README's "extra keys may be added" rule permits.

## Spec (issue #62)

| Criterion | Evidence | Status |
|---|---|---|
| `manager_cmd(prompt_path, cwd)` expands `{prompt}` with a path; prompt written to `<factory>/manager-prompt-<n>.md` | `factory/config.py:146-151`; `factory/manage.py:264-266` | met (write happens at the call site, consistent with the path-typed signature the issue itself specifies) |
| Bare `{prompt}` for `omp` → `ConfigError` naming `@{prompt}`; other programs not second-guessed | `factory/config.py:149-150` (exact-element match, `Path(argv[0]).name` handles absolute paths) | met |
| Default/template manager command uses worker shape | `factory/templates/factory.toml:65`, `README.md:220` | met |
| Doctor `manager command` row: WARN unset, FAIL missing placeholder/executable, PASS otherwise | `factory/onboard.py:226-238` | met; additionally FAILs bare `omp {prompt}` (`onboard.py:234-235`), consistent with `config.py:149` |
| Nonzero exit posts `Manager command exited N (argv: …unexpanded…)` + last 5 stderr lines | `factory/manage.py:268-272` (`json.dumps(cfg.manager)` is the unexpanded argv) | met |
| Records `escalate` event with `reason: manager_failed`; `factory stats` counts it | `factory/manage.py:274-275`; `factory/stats.py:167-174`, `stats.py:303` | met |
| Exit-gate tests: `@<path>` readable content; `ConfigError` on bare `{prompt}`; HUMAN body prefix + ≤5 stderr lines; doctor WARN/FAIL | `tests/test_factory.py:150-165`, `:410-436`, `:730-751`, `:753-792` | met |
| `ConfigError` contained per ticket instead of aborting the pass | `factory/manage.py:279-280`, test `:794-806` | met (round-1 reviewer fix) |
| `manager_failed` excluded from round arithmetic and from the "latest escalation" lookup, so the real packet is retried on the next pass without burning a round | `factory/dispatch.py:376-378`; `factory/manage.py:237-238`; `stats.py:167-168` | met |

Notes (not findings — no diff citation possible):
- Issue "Touches" names `DEFAULT_MANAGER` in `config.py`; the diff does not modify it. `[INFERENCE]` README's "unset command disables it" implies no code-level default exists, so nothing to change; if a `DEFAULT_MANAGER` constant with bare `{prompt}` does exist, it would now trip `config.py:149` on first use.
- `factory/learn.py:64-68` uses `NamedTemporaryFile(prefix="manager-prompt-")` rather than `manager-prompt-<n>.md`; `learn` has no ticket number, so the naming deviation is reasonable and still in `<factory>/`.
- The issue's "existing inline-text configs keep working" sentence contradicts its own next clause; the diff implements the explicit `ConfigError` behaviour, which the exit gate tests require.

VERDICT: APPROVE

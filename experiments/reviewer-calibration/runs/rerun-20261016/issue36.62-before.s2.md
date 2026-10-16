# Review: issue #62 — manager prompt-file transport

## Spec coverage (issue "After this change" / exit gate)

| Criterion | Evidence | Status |
|---|---|---|
| `manager_cmd(prompt_path, cwd)` expands `{prompt}` with a file path | `factory/config.py:146-151`; caller writes `<factory>/manager-prompt-<n>.md` at `factory/manage.py:263-265` | Met |
| Bare `{prompt}` for `omp` → `ConfigError` naming `@{prompt}` | `factory/config.py:149-150` (exact-element match, so `@{prompt}` passes; `Path(argv[0]).name` handles absolute paths); other programs untouched | Met |
| Nonzero exit → `Manager command exited N (argv: …unexpanded…)` + ≤5 stderr lines | `factory/manage.py:267-271` (`json.dumps(cfg.manager)` is the unexpanded template; `splitlines()[-5:]`) | Met |
| `escalate` event with `reason: manager_failed` | `factory/manage.py:273-274` | Met |
| `manager_failed` must not consume an escalation round (round-1 reviewer fix) | `factory/dispatch.py:376-377` | Met |
| `ConfigError` contained per ticket instead of aborting the pass (round-1 reviewer fix) | `factory/manage.py:278-279` | Met |
| `factory doctor` row: WARN unset / FAIL missing placeholder or executable / PASS | `factory/onboard.py:226-238` | Met |
| README + template document file-vs-text contract | `README.md:37,97-104,215-216`; `factory/templates/factory.toml:25,51,63-65` | Met |
| Tests: `@<path>` readable content, ConfigError regex, HUMAN body prefix + 5-line cap, doctor rows | `tests/test_factory.py:150-165,410-436,730-797` | Met |
| `learn` adapter migrated to file transport | `factory/learn.py:64-68` (`NamedTemporaryFile` kept open while subprocess reads — valid on Linux) | Met |

Gate: test PASS on target host; not re-run here.

## Required fixes

None.

## Optional suggestions

1. **Template FIX profiles lose their instruction text** — `factory/templates/factory.toml:44,48`. The old `ci-fix`/`conflict` commands carried profile-specific guidance inline ("declare a flake with concrete log and run evidence", "restart [the rebase] against the configured main"). They were worker commands, not subject to the new manager validation, and were not named in the issue's Touches beyond documenting `{prompt}`. Replacing them with `@{prompt}` removes that guidance from the template; the `when` rule survives but the worker no longer receives those instructions from its argv. Consider restoring the original lines (they were already valid: inline text referencing the path). Not blocking: commented-out example lines, no rule cited.

2. **`DEFAULT_MANAGER` not visible in diff** — the AC says "The default manager command becomes the worker shape" and Touches lists `config.py (DEFAULT_MANAGER)`, but the diff only changes `manager_cmd` (`factory/config.py:146-151`). If a `DEFAULT_MANAGER` constant with bare `{prompt}` for `omp` exists and is consumed anywhere, it now trips the new `ConfigError`. `[INFERENCE]` — cannot confirm from the packet; `cfg.manager` is nullable ("unset disables it"), suggesting no runtime default. Worth a one-line check before merge; not blocking without evidence.

3. **Nonzero-exit non-replay untested** — `tests/test_factory.py:752-797` patches `manage.apply`, so the `manage` event that suppresses replay is never written on that path; only the `ConfigError` path (`tests/test_factory.py:786-797`) proves no second comment. Same downstream code, so low risk.

4. **Doctor FAIL for `omp` + bare `{prompt}`** — `factory/onboard.py:233-234`. Beyond the brief's three doctor states but mirrors `config.py:149` and surfaces the runtime rejection at `doctor` time, which is the issue's stated leverage point #1. Justified; no change requested.

VERDICT: APPROVE

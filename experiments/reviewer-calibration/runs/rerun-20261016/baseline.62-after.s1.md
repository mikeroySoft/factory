## Spec (issue #62)

| Criterion | Evidence | Status |
|---|---|---|
| `manager_cmd(prompt_path, cwd)` expands `{prompt}` with a path; prompt written to `<factory>/manager-prompt-<n>.md` | `factory/config.py:146-151`, `factory/manage.py:264-266` | met |
| Bare `{prompt}` for `omp` → `ConfigError` naming `@{prompt}`; other programs untouched | `factory/config.py:149-150` (`Path(argv[0]).name == "omp"` guard) | met |
| Doctor `manager command` row: WARN unset / FAIL missing placeholder or executable / PASS | `factory/onboard.py:226-238` | met |
| Nonzero exit → `Manager command exited N (argv: …unexpanded…)` + ≤5 stderr lines; `escalate` event `reason: manager_failed`; countable by `factory stats` | `factory/manage.py:268-275`, `factory/stats.py:167-173`, `factory/stats.py:303` | met |
| README §Configuration + `factory init` template: `{prompt}` = file for workers and manager, text for reviewer | `README.md:97-101`, `README.md:219-220`, `factory/templates/factory.toml:25`, `:51`, `:63-65` | met |
| Exit-gate tests: `@<path>` readable with prompt text; bare `{prompt}` raises; stub exit 1 → body prefix + 5 lines; doctor WARN/FAIL | `tests/test_factory.py:150-165`, `:410-436`, `:730-751`, `:753-787` | met |
| Out of scope untouched (decision semantics, packet format, model) | no changes to `parse`/packet builders in diff | met |

Additional behaviour beyond the issue text, consistent with the handoff's prior review round: `manager_failed` events excluded from round arithmetic (`factory/dispatch.py:376-377`, `factory/manage.py:237-238`) and `ConfigError` contained at the execution boundary (`factory/manage.py:279`). Both have regressions (`tests/test_factory.py:776-787`, `:790-802`) and neither contradicts an acceptance criterion.

## Standards (README)

- README "every finding cites `path:line` … Code validates the output, records a `manage` event before GitHub mutations" — the failure path still flows into the existing `manage`-event/`apply` sequence (`factory/manage.py:268-275` sets `decision, data` and falls through), so the documented ordering is preserved.
- CHANGELOG `Unreleased` entry present (`CHANGELOG.md:4-6`); README stats section updated for the new column (`README.md:144-146`).

## Non-blocking notes

- Issue "Touches" lists `DEFAULT_MANAGER` in `config.py`; the diff does not modify any such constant. Not citable from the diff; README (`factory/templates/factory.toml:61`) states an unset command disables the manager, so a text-shaped default may not exist. `[INFERENCE]`
- `factory/manage.py:273-274` records the `manager_failed` event before the `human_activity` check at `:282`; a human takeover still leaves the failure counted in `manager_failures`. Matches the test at `tests/test_factory.py:835-859` (`apply.assert_not_called()`); event is evidence, not a mutation.
- `factory/learn.py:66-68` uses `NamedTemporaryFile(... suffix=".md")` under `cfg.factory`, so a stray `manager-prompt-*.md` from `learn` cannot collide with `manager-prompt-<n>.md` from `manage` (random infix). Fine.

VERDICT: APPROVE

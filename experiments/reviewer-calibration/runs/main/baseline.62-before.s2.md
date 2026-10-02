# Review: issue #62 — manager prompt-file transport

## Spec (issue #62 acceptance criteria)

| Criterion | Evidence | Status |
|---|---|---|
| `manager_cmd(prompt_path, cwd)` expands `{prompt}` with a file path; prompt written to `<factory>/manager-prompt-<n>.md` | `factory/config.py:146-151`; `factory/manage.py:263-265` writes `cfg.factory / f"manager-prompt-{n}.md"` before expansion | Met |
| Bare `{prompt}` for `omp` argv[0] → `ConfigError` naming `@{prompt}`; other programs not second-guessed | `factory/config.py:148-149` (`Path(argv[0]).name == "omp"` and exact-element match, so `@{prompt}` and non-omp commands pass) | Met |
| README §Configuration + `factory init` template: `{prompt}` = file for workers and manager, text for reviewer | `README.md:97-100`, `README.md:215-216`; `factory/templates/factory.toml:25`, `:50`, `:63-65` | Met |
| Default manager command is worker shape | `factory/templates/factory.toml:65`, `README.md:216`. No `DEFAULT_MANAGER` constant appears in the diff; `factory/config.py:147` (`self.manager or []`) indicates no built-in default exists, so the template/README are the effective default. `[INFERENCE]` — issue's "Touches" list may have named a nonexistent symbol. | Met |
| `factory doctor` `manager command` row: WARN unset, FAIL missing placeholder/executable, PASS otherwise | `factory/onboard.py:226-238` | Met |
| Nonzero exit posts `Manager command exited N (argv: …unexpanded…)` + last 5 stderr lines; records `escalate` event `reason: manager_failed` | `factory/manage.py:266-272` (`json.dumps(cfg.manager)` is the unexpanded argv; `splitlines()[-5:]`) | Met |
| Exit-gate tests: `@<path>` readable with prompt text; bare `{prompt}` raises; stub exit 1 → body prefix + ≤5 lines; doctor WARN/FAIL | `tests/test_factory.py:150-165`, `:410-436`, `:730-749`, `:751-784` | Met |

Additional correctness points checked:

- `factory/manage.py:278` catches `config.ConfigError` at the execution boundary, so a stale host config degrades to a HUMAN diagnosis instead of aborting the pass; regression at `tests/test_factory.py:786-798`.
- `factory/dispatch.py:376-377` excludes `manager_failed` from round arithmetic, so the new event does not consume a manager round; asserted at `tests/test_factory.py:772-784` (`round_number == 2`, RETRY applied on next pass).
- `factory/learn.py:64-68`: `NamedTemporaryFile` stays open across the synchronous `dispatch.run`, so the path is valid for the whole invocation.
- Existing test stubs updated to read the path (`tests/test_factory.py:861`, `:983`, `:1219`) — consistent with the contract change.

## Standards (README conventions)

No AGENTS.md/CONTRIBUTING.md at this head. README text updated in both the manager-contract paragraph and the Configuration example; template matches README. No convention violations found.

## Optional (non-blocking)

- `factory/templates/factory.toml:44`, `:48`: the `ci-fix`/`conflict` example commands previously embedded task-specific instructions ("declare a flake with evidence…", "preserve both sides' intents…") as inline text alongside the `{prompt}` path. The rewrite to `@{prompt}` drops that guidance; the `when` rules remain, but the worker no longer receives those instructions from the example command. Not required by the issue; consider restoring the guidance via another channel if those profiles depend on it.

VERDICT: APPROVE

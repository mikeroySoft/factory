# Review — issue #62, head `68b5d16`

## Spec (acceptance criteria)

| Criterion | Evidence | Status |
|---|---|---|
| `manager_cmd(prompt_path, cwd)` expands `{prompt}` with a path; prompt written to `<factory>/manager-prompt-<n>.md` | `factory/config.py:146-151`; `factory/manage.py:264-266` writes `cfg.factory / f"manager-prompt-{n}.md"` | Met |
| Bare `{prompt}` for `omp` → `ConfigError` naming `@{prompt}`; other programs untouched | `factory/config.py:149-150` (checks `Path(argv[0]).name == "omp"` only) | Met |
| Default/template manager command is worker shape | `factory/templates/factory.toml:63-65`; `README.md:219-220` | Met (template + README; no `DEFAULT_MANAGER` constant appears in the diff — README states unset disables the manager, so the documented template is the effective default) |
| README §Configuration + init template document `{prompt}` = file for workers and manager, text for reviewer | `README.md:97-101`, `README.md:219-220`; `factory/templates/factory.toml:25`, `:51`, `:63-64` | Met |
| `factory doctor` `manager command` row: WARN unset, FAIL missing placeholder/executable, PASS otherwise | `factory/onboard.py:226-238` | Met |
| Nonzero exit posts `Manager command exited N (argv: …unexpanded…)` + last 5 stderr lines | `factory/manage.py:268-271` (`json.dumps(cfg.manager)` is the unexpanded argv; `splitlines()[-5:]`) | Met |
| Records `escalate` event with `reason: manager_failed` for stats | `factory/manage.py:273-274`; counted in `factory/stats.py:171-172` | Met |
| Exit-gate test: District-shaped `@{prompt}` → `@<path>` readable with prompt text; bare `{prompt}` raises | `tests/test_factory.py:150-165` | Met |
| Exit-gate test: stub exits 1, HUMAN body starts with `Manager command exited 1`, ≤5 stderr lines | `tests/test_factory.py:752-767` | Met |
| Exit-gate: doctor WARN with no manager, FAIL without placeholders | `tests/test_factory.py:410-436` (both JSON and text rows) | Met |

Additional correctness checks on the diff:

- `manager_failed` events excluded from round arithmetic and from escalation lookup, so a manager failure doesn't consume a round or replay (`factory/dispatch.py:375-377`, `factory/manage.py:237-238`); regression at `tests/test_factory.py:771-782`.
- `ConfigError` is caught at the execution boundary so a legacy bare-`{prompt}` host config yields a HUMAN diagnosis instead of aborting the pass (`factory/manage.py:279`; test `tests/test_factory.py:784-797`).
- `factory learn` migrated to the file transport via `NamedTemporaryFile` in `cfg.factory` (`factory/learn.py:64-68`); file stays open during the subprocess, so the path is readable on Linux.
- Existing tests whose stub manager echoed `sys.argv[2]` as text were updated to read the path (`tests/test_factory.py:891`, `:1013`, `:1249`) — consistent with the cutover, no stale callers.

## Standards

No AGENTS.md/CONTRIBUTING.md at this head. README conventions exercised by the diff (README §Configuration template, `[manager]` block, CHANGELOG entry `CHANGELOG.md:4-6`) are followed. Nothing in the diff contradicts documented behaviour.

## Not verifiable from the packet

- Manual District #26 kept-packet replay (handoff claims a prior `DECISION: FIX`); outside the diff.
- Whether a `DEFAULT_MANAGER` constant exists in `config.py` outside the hunk; the issue names it but the README documents "unset disables it", so the updated template is the effective default.

No findings that fail an acceptance criterion or a documented convention.

VERDICT: APPROVE

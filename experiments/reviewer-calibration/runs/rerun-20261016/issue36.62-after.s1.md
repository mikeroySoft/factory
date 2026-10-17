# Review — issue #62, head `68b5d16`

## Spec coverage (issue "After this change" + exit gate)

| Criterion | Evidence | Status |
|---|---|---|
| `manager_cmd(prompt_path, cwd)` expands `{prompt}` with a file path | `factory/config.py:146-151`; caller writes `<factory>/manager-prompt-<n>.md` at `factory/manage.py:264-266` | Met (write moved to caller; exit-gate test asserts `@<path>` readable with prompt text, `tests/test_factory.py:150-165`) |
| Bare `{prompt}` for `omp` → `ConfigError` naming `@{prompt}`; other programs not second-guessed | `factory/config.py:149-150` (`Path(argv[0]).name == "omp" and "{prompt}" in argv`, exact-element match) | Met |
| README §Configuration + init template document file for workers/manager, text for reviewer | `README.md:97-106`, `README.md:219-220`, `factory/templates/factory.toml:25`, `:51`, `:63-65` | Met |
| Default manager command becomes worker shape | Template `factory/templates/factory.toml:65` | Met for the template; see Optional 1 |
| `doctor` row: PASS / FAIL naming placeholder or executable / WARN when unset | `factory/onboard.py:226-238`; test matrix `tests/test_factory.py:410-436` | Met |
| Nonzero exit → `Manager command exited N (argv: …unexpanded…)` + ≤5 stderr lines, `escalate` event `reason: manager_failed` | `factory/manage.py:267-274` (`json.dumps(cfg.manager)` is the unexpanded argv); test `tests/test_factory.py:752-787` | Met |
| `factory stats` can count it | `factory/stats.py:167-174`, `:303` | Met |
| Round-1 reviewer required fixes (ConfigError contained per ticket; `manager_failed` excluded from round arithmetic) | `factory/manage.py:279`; `factory/dispatch.py:375-377`; `factory/manage.py:237-238` | Met; tests `tests/test_factory.py:789-802`, `:780-787` |
| `factory learn` uses the same transport | `factory/learn.py:64-68` — temp file lives for the duration of `dispatch.run` (inside `with`) | Met |

## Standards

No AGENTS.md/CONTRIBUTING.md at this head. README conventions followed: config documented in both README and template, CHANGELOG `Unreleased` entry present (`CHANGELOG.md:4-6`), `doctor` row shape matches existing `report(...)` usage.

## Required fixes

None.

## Optional suggestions

1. **`DEFAULT_MANAGER` not touched.** The issue's "Touches" lists `DEFAULT_MANAGER` in `factory/config.py`, but the diff only changes `manager_cmd`. If `config.py` defines a `DEFAULT_MANAGER` constant with bare `omp … "{prompt}"`, that default now raises `ConfigError` at `factory/config.py:149-150`. `[INFERENCE]` — the README says an unset command disables the manager, so a code-level default may not exist; cannot confirm from the diff. Worth a one-line check before merge.
2. **Stdout dropped on nonzero exit.** `factory/manage.py:269` tails only `proc.stderr`; the previous body used `proc.stderr or proc.stdout`. A CLI that reports failure on stdout now yields a body with no diagnostic lines. Matches the issue wording ("stderr tail"), so not blocking.
3. **`manager-prompt-<n>.md` is never removed** (`factory/manage.py:264-265`); it accumulates in `.factory/` per ticket. Harmless (gitignored), and useful for forensics — mention only.
4. `tests/test_factory.py:752-787` covers five distinct behaviours in one test (body shape, stderr cap, event fields, stats, round arithmetic, next-pass RETRY). Splitting would make failures more locatable; not a rule here.

VERDICT: APPROVE

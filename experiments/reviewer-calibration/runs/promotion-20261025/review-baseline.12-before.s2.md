## Evidence basis

`./review.diff` and `./snapshot` are absent from the sandbox (cwd contains only `repo/` at `4830b7d`). Diff reconstructed from the sibling gated checkout (`/tmp/cal-tools-12-after-tr87nvj6/repo`, head `7cd7a83 Preserve legacy manager command parsing during integration`, same author/branch). Reviewed the full ticket delta `6ec3546 (main)..7cd7a83`; the `4830b7d..7cd7a83` tail touches only `factory/config.py`, `tests/test_briefing.py`, `tests/test_factory.py`. Line cites are head-side. Issue #12 fetched via GitHub API (gh CLI unauthenticated).

## 1. Standards

Repo has no `AGENTS.md`/`CONTRIBUTING.md`; conventions are `pyproject.toml` (`[tool.ruff] line-length = 100`, default rule set — E501 not selected) and CI = `python -m unittest discover -s tests` (`.github/workflows/ci.yml`, `.factory.toml`).

- Host-layer pattern matched: `[manager]` already in `HOST_TABLES` (`factory/config.py:50`); `KNOWN_KEYS` extended (`factory/config.py:60`); loader reads `manager` table alongside the other tables (`factory/config.py:271,287-291`) using the same `int(table.get(...))` / `table.get(...)` idiom as `gate.timeout`/`dashboard.port`.
- Doctor row follows the existing `reviewer:` `report(shutil.which(...))` shape (`factory/onboard.py:226-227`).
- Tests are `unittest`, placed in existing `HostConfigTest`/loader test (`tests/test_factory.py:112-115,137-138,276,294`). No violations.
- Smell check: `manager_model` → `manager_settings` returning `(command, model)` (`factory/config.py:224-252`) consolidates argv normalization already present for `--model` extraction (shlex string → argv, type check) so `cfg.manager` reuses it rather than duplicating the parse — justified, not speculative generality. Tuple return is a judgment call, not a defect.

## 2. Spec (issue #12 / `docs/manager-plan.md` T3)

| Criterion | Status | Evidence |
|---|---|---|
| `command = [...]` argv template, unset = disabled | ✓ | `factory/config.py:104` default `None`; `:287` |
| `rounds = 1` default | ✓ | `factory/config.py:105,288` |
| `review = "escalated"`, `"all"` allowed | ✓ | `factory/config.py:106,289-291` rejects other values |
| Host-layer like `review` (`HOST_TABLES`) | ✓ | `factory/config.py:50` (pre-existing), `KNOWN_KEYS` `:60` |
| `factory doctor` prints `manager:` row when configured, nothing otherwise | ✓ | `factory/onboard.py:226-227`; test `tests/test_factory.py:294-302` |
| Unknown `[manager]` keys reported as drift | ✓ | `tests/test_factory.py:263` (`manager.unknown`) via `unknown_keys`/`KNOWN_KEYS` |
| `dashboard --json` `config` includes table | ✓ | `factory/dashboard.py:840-844` |
| `templates/factory.toml` documents it | ✓ | `factory/templates/factory.toml:43-47` |
| Loader test for all three keys | ✓ | `tests/test_factory.py:112-115,137-138` |
| Out of scope: running the command | ✓ | no execution path added; `tests/test_briefing.py:116-124` asserts non-execution |

Scope creep: none. Rename of `manager_model` forced the `test_briefing.py` edit (`:116-124`); no behavior change there.

## 3. Plan drift

Worker plan not recorded → no plan-named file list or planned proof to compare. Files changed vs. issue "Touches": `factory/config.py`, `factory/dashboard.py`, `factory/onboard.py`, `factory/templates/factory.toml` (all named); `tests/test_factory.py` (required by exit gate), `tests/test_briefing.py` (consequence of rename). Gate report covers `test: PASS`; no planned proof is identifiable as missing. No drift exposes a defect.

## Required fixes

None.

## Optional suggestions

- `factory/config.py:288` — `rounds = "x"` / `rounds = -1` surface as `ValueError` traceback or are accepted, unlike `review` which raises `ConfigError` (`:290-291`). Same idiom as `gate.timeout`/`dashboard.port`, so consistent with repo; tighten only if T4 needs a guaranteed positive int.
- `factory/onboard.py:227` — `shutil.which(cfg.manager[0])` yields a FAIL row if argv[0] is a `{cwd}`-relative placeholder; no documented rule requires otherwise.

VERDICT: APPROVE

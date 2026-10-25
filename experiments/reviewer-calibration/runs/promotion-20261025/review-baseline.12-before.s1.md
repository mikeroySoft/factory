## Standards

Sources: `snapshot/skills/factory/SKILL.md` (no `AGENTS.md`/`CONTRIBUTING.md` in snapshot; `docs/` holds only `manager-plan.md`). No documented-rule violations. The diff follows the loader's existing per-table pattern (`review.command` → `list(...)`, `int(...)` for numerics, `KNOWN_KEYS` drives drift), and the template documents every new key as SKILL.md line 24 expects.

- Duplicated Code (judgement call, non-blocking): `review.diff:28` and `snapshot/factory/config.py:302` both do `raw.get("manager", {})`; the local `manager` could be passed to `manager_model`. Cosmetic.

## Spec (issue #12 + agent brief)

| Criterion | Status | Evidence |
|---|---|---|
| `KNOWN_KEYS` lists `command`, `rounds`, `review` | met | `review.diff:10` |
| `HOST_TABLES` includes `manager` | met (pre-existing) | `snapshot/factory/config.py:50` |
| `Config` + loader: `command` unset = disabled, `rounds=1`, `review="escalated"` | met | `review.diff:18-20,36-39` |
| doctor prints `manager:` row only when configured | met | `review.diff:67-68`, test `review.diff:139-147` |
| unknown `[manager]` keys reported as drift | met | test `review.diff:123,131` |
| `dashboard --json` `config` includes table | met | `review.diff:51-55` |
| template documents it | met | `review.diff:85-90` |
| loader test for all three keys | met | `review.diff:102-105,113-114` |
| running the command out of scope | respected | no execution path added |

### Required fixes

1. **String-form `command` silently loads as a list of characters.** `review.diff:36-37` does `cfg.manager = list(manager["command"])`. The same loader, 16 lines later (`snapshot/factory/config.py:302` → `manager_model`, lines 231-237), explicitly accepts `command` as a string (`shlex.split`) and its error text advertises "argv array or command string". Trigger: `[manager]\ncommand = "omp -p {prompt}"` — `load()` succeeds with no `ConfigError`. Impact: `cfg.manager == ['o','m','p',' ','-','p',...]`; `factory doctor` (`review.diff:68`) reports `manager: o` and PASS/FAIL depends on `which o`; `dashboard --json` `config.manager.command` (`review.diff:52`) shows `"o m p   - p ..."`. Both exit-gate surfaces (doctor row, dashboard config) emit wrong data for an input the loader accepts. One-line fix, either direction: `shlex.split` when `isinstance(cmd, str)` (consistent with `manager_model`), or raise `ConfigError` for non-list `command` so the two readers of the same key agree. No new abstraction needed.

### Optional suggestions

- `review.diff:39`: `review` accepts any string; issue allows only `"escalated"`/`"all"`. Issue does not require rejection, so non-blocking; a `ConfigError` on other values would surface typos at `doctor` time instead of at T5.
- `review.diff:28` vs `snapshot/factory/config.py:302`: pass the local `manager` to `manager_model` instead of re-reading `raw`.

## Plan drift

Plan status not recorded; no planned file list or proof list to diff against. Files changed (`config.py`, `onboard.py`, `templates/factory.toml`, `dashboard.py`, `tests/test_factory.py`) match the issue's Touches plus the required test. Gate `test: PASS` covers the issue's verification command. No drift to report; nothing exposes a defect beyond Required #1.

VERDICT: REVISE

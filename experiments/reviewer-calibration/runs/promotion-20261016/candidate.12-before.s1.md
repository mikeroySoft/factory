## Scope

Diff: `git diff origin/main..HEAD` (4 commits, 5 files). Spec: issue #12 body + triage comment (agent brief). Standards sources: no `AGENTS.md`/`CONTRIBUTING.md` in repo; `README.md` and `docs/manager-plan.md` T3 are the only documentation.

## Standards

No documented-rule violations. The loader additions mirror the existing `[review]` shape (`config.py:283-288`), the template follows the commented-default convention (`templates/factory.toml:46-48`), and the doctor row reuses `report()` like the reviewer row (`onboard.py:225-227`). No baseline smells worth flagging.

## Spec

| Criterion (issue #12 / brief) | Evidence | Status |
|---|---|---|
| `command` argv template, unset = disabled | `config.py:104,285-286`; `dashboard.py:841` emits `None` when unset | Met |
| `rounds = 1` default, `review = "escalated"` default | `config.py:105-106,287-288` | Met |
| Host-layer table like `review` (`HOST_TABLES`) | Already present on main: `config.py:50`; flows via `host_filter` `config.py:191` | Met (pre-existing) |
| `KNOWN_KEYS` registers all three keys | `config.py:60` | Met |
| `doctor` prints `manager:` row only when configured | `onboard.py:226-227`; test `test_factory.py:276-284` | Met |
| Unknown keys under `[manager]` reported as drift | `unknown_keys` unchanged, test `test_factory.py:252,263` | Met |
| `dashboard --json` `config` includes the table | `dashboard.py:840-844` | Met |
| `templates/factory.toml` documents it | `templates/factory.toml:43-48` | Met |
| Loader test for all three keys | `test_factory.py:112-115,137-138` | Met |
| Out of scope: running the command | No execution path added | Met |

## Required fixes

### 1. `config.py:285-288` — string-form `manager.command` is silently mangled; non-table `[manager]` now crashes with a traceback instead of `ConfigError`

Defect, not spec: main already accepts `manager.command` as a string (`manager_model`, `config.py:231-233`, documented as "existing manager.command" in `README.md:171-172`). On main that config loaded cleanly. After this diff:

- Trigger A: `[manager] command = "omp -p --model x"`. `config.py:286` does `list("omp -p ...")` → `['o','m','p',' ', ...]`. Impact: `factory doctor` reports `manager: o` FAIL (`onboard.py:227`), so `doctor` exits nonzero on a config that passed before; `dashboard --json` shows `"command": "o m p   - p ..."` (`dashboard.py:841`).
- Trigger B: `manager = 5` (or any non-table). `config.py:285` `"command" in 5` raises `TypeError` before `config.py:302` gets to raise the existing `ConfigError("[manager] must be a table")`. Impact: existing explicit validation is bypassed; user sees a traceback instead of the `factory: ...` message.

Minimal fix: move `cfg.manager_model = manager_model(manager)` (`config.py:302`) above `config.py:285` so the table check runs first, and either reject a string `command` with `ConfigError` or `shlex.split` it the same way `manager_model` does. No new abstraction needed.

## Optional suggestions

- `config.py:288` — `manager.review` is not constrained to `"escalated"|"all"`; a typo (`"al"`) loads silently. Spec names exactly two values but does not require value validation, and no other loader key validates values, so non-blocking.
- `config.py:302` — can pass the `manager` local instead of re-reading `raw.get("manager", {})`; falls out naturally from the required fix.
- `dashboard.html:884-886` renders `reviewer` but not the new `config.manager`; spec only requires the JSON, so non-blocking.
- `README.md:121-156` config example omits `[manager]` `rounds`/`review`; spec names only the template, so non-blocking.

## Changed surface → consumers outside the diff

- `KNOWN_KEYS["manager"]` (`config.py:60`): `unknown_keys` `config.py:207-221` — holds (rounds/review no longer flagged, `unknown` still is); `onboard.py:139-143` missing-key scan skips `HOST_TABLES` so manager excluded — holds; `onboard.py:149-151` host-section scan — holds.
- `Config.manager` (`config.py:104`): `onboard.py:226-227`, `dashboard.py:841` only — hold modulo Required #1.
- `Config.manager_rounds` / `manager_review` (`config.py:105-106`): `dashboard.py:842-843` only — hold. `docs/manager-plan.md:171` already names `manager_rounds`; consistent.
- `templates/factory.toml`: copied verbatim by `factory init` (`onboard.py:82`); all additions are comments, so a fresh init loads identically.
- Dashboard `config.manager` key: `dashboard.html:875-888` does not read it; no test pins the config key set — holds.
- `HOST_TABLES` (`config.py:50`): unchanged by diff; `host_filter` `config.py:191` and `onboard.py:131,140,149` — hold.

VERDICT: REVISE

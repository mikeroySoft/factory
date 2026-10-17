## Standards

Sources: no `AGENTS.md`/`CONTRIBUTING.md` in repo; `docs/manager-plan.md` §6 T3 is the only documented convention source. The diff mirrors the existing `review`-table pattern (`factory/config.py:59` vs `:60`, `:283-284` vs `:285-288`), so no documented-standard breach.

Smell baseline (judgement calls only):
- Possible Duplicated Code: `factory/config.py:269` reads `raw.get("manager", {})` into `manager`, and `:302` re-reads `raw.get("manager", {})` for `manager_model(...)`. Could pass `manager`. Non-blocking.

## Spec

Issue #12 exit gate vs diff:

| Criterion | Evidence | Status |
|---|---|---|
| `KNOWN_KEYS` gets `command`, `rounds`, `review` | `factory/config.py:60` | met |
| `HOST_TABLES` has `manager` | already on main (`factory/config.py:50`) | met |
| `Config` + loader for three keys, `command` unset = disabled | `factory/config.py:104-106`, `:285-288` | met |
| doctor `manager:` row only when configured | `factory/onboard.py:226-227`; test `tests/test_factory.py:276-284` | met |
| unknown `[manager]` keys reported as drift | test `tests/test_factory.py:252`, `:263` | met |
| `dashboard --json` `config` includes table | `factory/dashboard.py:840-844` | met |
| template documents it | `factory/templates/factory.toml:43-48` | met |
| loader test for all three keys | `tests/test_factory.py:112-115`, `:137-138` | met |
| Out of scope: running the command | no execution path added | met |

### Required fixes

1. **`factory/config.py:285-286` — string-form `manager.command` is silently exploded into characters.** `manager_model` (`factory/config.py:231-237`, unchanged) explicitly accepts `command` as a string (`shlex.split`; error text "argv array or command string"), so `[manager] command = "omp -p --model x"` loads without error on main. The new loader runs `list(manager["command"])` before that validation, producing `cfg.manager = ['o','m','p',' ', ...]`. Impact: `factory doctor` prints `manager: o` → FAIL (`factory/onboard.py:227`), and `dashboard --json` reports `"command": "o m p   - p ..."` (`factory/dashboard.py:841`). Trigger is an input the same module declares valid; gate tests only use the array form. Fix is one line either way: `shlex.split` when `isinstance(command, str)` (consistent with `manager_model`), or reject non-list with `ConfigError`.

### Optional suggestions

- `factory/config.py:288`: `review` accepts any string; spec names only `"escalated"`/`"all"`. A `ConfigError` on other values would catch typos (`"esclated"`) that otherwise pass doctor silently. Not required by the exit gate.
- `factory/config.py:269`/`:302`: pass the already-bound `manager` dict to `manager_model` instead of re-reading `raw`.
- `tests/test_factory.py:281`: `lambda` + `# noqa: E731` for a two-use helper; a nested `def` avoids the noqa. Style only.

Summary — Standards: 1 judgement-call smell, no hard violations. Spec: all exit-gate criteria met; 1 required fix (string-form `command` mishandled before existing validation).

VERDICT: REVISE

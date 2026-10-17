## Standards

Standards sources: no in-repo `AGENTS.md`/`CONTRIBUTING.md`; `README.md` config docs, `factory/templates/factory.toml`, existing loader patterns in `factory/config.py`.

- `factory/config.py:60,104-106,285-288` — follows existing `KNOWN_KEYS` / `Config` field / `int(table.get(...))` loader shape used by `review`, `gate`, `dashboard`. `manager` was already in `HOST_TABLES` (`config.py:50`, on main), so no change needed there.
- `factory/dashboard.py:840-844`, `factory/onboard.py:226-227` — mirror the `reviewer` rows exactly. No new abstraction introduced; no `manager_cmd()`/`expand` wrapper added, consistent with "running the command" being out of scope.
- `tests/test_factory.py:112-115,137-138,252,263,276-284` — matches existing `stub_bin`/`doctor` lambda idiom (`test_factory.py:234`).
- Smell baseline: nothing flagged. The `manager` / `manager_rounds` / `manager_review` triplet on `Config` is a mild Data Clump but matches how every other table is flattened onto `Config` (repo convention wins).

## Spec

Issue #12 exit gate, checked against the diff:

| Criterion | Evidence | Status |
|---|---|---|
| `command` argv template, unset = disabled | `config.py:104,285-286` | met (see Required #1 for one input form) |
| `rounds = 1` default | `config.py:105,287` | met |
| `review = "escalated"`, `"all"` allowed | `config.py:106,288` | met (no rejection of other values; optional) |
| `HOST_TABLES` / `KNOWN_KEYS` | `config.py:60`; `HOST_TABLES` pre-existing | met |
| doctor prints `manager:` only when configured | `onboard.py:226-227`, test `test_factory.py:276-284` | met |
| unknown `[manager]` keys reported as drift | `test_factory.py:252,263` | met |
| `dashboard --json` `config` includes it | `dashboard.py:840-844` | met |
| template documents it | `factory.toml:43-48` | met |
| loader test for all three keys | `test_factory.py:112-115,137-138` | met |
| Out of scope: running the command | nothing executes `cfg.manager` | met |

### Required fixes

1. **`factory/config.py:285-286` — string-form `manager.command` becomes a list of characters.**
   `manager_model()` (main, `config.py:231-237`) explicitly accepts `command` as either an argv array *or* a command string (`"manager.command must be an argv array or command string"`), and it runs *after* the new loader lines (`config.py:302`). So `[manager] command = "omp -p --model x {prompt}"` passes validation, but `list(manager["command"])` yields `['o','m','p',' ', ...]`. Trigger: any host/repo config using the string form the validator permits. Impact: `factory doctor` reports `manager: o` → FAIL (`onboard.py:227`), and `dashboard --json` `config.manager.command` is `"o m p   - p ..."` (`dashboard.py:841`); both violate the exit-gate criterion "doctor reports the binary". Fix is one line: split strings the same way `manager_model` does (`shlex.split(cmd) if isinstance(cmd, str) else list(cmd)`), or make `manager_model` reject strings so both paths agree. `[INFERENCE]` from code reading only; not executed per instructions.

### Optional suggestions

- `factory/config.py:288` — `review` accepts any value; the issue says `"escalated"` default, `"all"` allowed. Since nothing consumes it yet (running is out of scope), a `ConfigError` for other values is reasonable but not required by the issue text. Add it when T4/T5 consume the value.
- `factory/config.py:287` — `rounds` has no lower bound (`0`/negative accepted). Same reasoning: harmless until consumed.
- `factory/config.py:269,302` — `manager` local is already bound; `manager_model(raw.get("manager", {}))` could reuse it. Cosmetic.

Summary — Standards: 0 findings. Spec: 1 required (string-form `command` mishandled → doctor/dashboard show a bogus binary), 3 optional.

VERDICT: REVISE

Review complete. Diff traced end-to-end; one concrete defect found at the interface between the new loader lines and the pre-existing `[manager]` validator.

## Standards

No `AGENTS.md`/`CONTRIBUTING.md` in this repo; standards = `README.md` + established loader patterns. The diff mirrors the `review` table handling (`config.py:283-284` → `285-286`, `onboard.py:225` → `226-227`, `dashboard.py:839` → `840-844`). No documented-rule violations.

Smell baseline (judgement calls only):
- **Duplicated lookup** — `config.py:269` binds `manager = raw.get("manager", {})`, but `config.py:302` still calls `manager_model(raw.get("manager", {}))`. Pass `manager`. Trivial.

## Spec (issue #12 / `docs/manager-plan.md` T3)

| Criterion | Status | Evidence |
|---|---|---|
| `command` argv template, unset = disabled | met | `config.py:104,285-286`; `onboard.py:226` |
| `rounds = 1`, `review = "escalated"` defaults | met | `config.py:105-106` |
| Host-layer via `HOST_TABLES` | met (pre-existing on main) | `config.py:50` |
| `KNOWN_KEYS` extended | met | `config.py:60` |
| `doctor` prints `manager:` row only when configured | met | `onboard.py:226-227`; test `tests/test_factory.py:276-284` |
| Unknown `[manager]` keys are drift | met | `tests/test_factory.py:252,263` |
| `dashboard --json` `config` includes table | met | `dashboard.py:840-844` |
| Template documents it | met | `factory/templates/factory.toml:43-48` |
| Loader test for all three keys | met | `tests/test_factory.py:112-115,137-138` |
| Out of scope: running it | respected | no exec path added |

### Required fixes

**1. String-form `command` is char-split; `doctor` reports the wrong binary.** — `factory/config.py:286`

The pre-existing validator `manager_model()` (`config.py:231-237`, unchanged) explicitly accepts `command` as either an argv array **or a command string** (error text: `"manager.command must be an argv array or command string"`; strings go through `shlex.split`). The new loader line does `cfg.manager = list(manager["command"])` unconditionally.

- Trigger: `[manager] command = "omp -p --model x {prompt}"` — an input `manager_model()` accepts without error, and one that pre-existing host configs may already carry (the docstring calls it "legacy manager argv").
- Impact: `cfg.manager == ['o','m','p',' ','-','p', …]`. `onboard.py:227` then runs `shutil.which("o")` → FAIL row `manager: o`, violating acceptance criterion "`factory doctor` reports the binary". `dashboard.py:841` emits `"o m p   - p …"`. T4 would later exec that argv.
- Secondary: `config.py:285-288` execute before `manager_model()` at `config.py:302`, so a non-table `manager = "x"` or `command = 5` now dies with `AttributeError`/`TypeError` instead of the existing `ConfigError` (`config.py:226-227,236-237`).

Smallest fix, no new abstraction: move lines 285-288 below line 302 (so the existing validator runs first) and normalize once:
```python
cmd = manager.get("command")
cfg.manager = shlex.split(cmd) if isinstance(cmd, str) else (list(cmd) if cmd is not None else None)
```
`shlex` is already imported for `manager_model`. Alternatively, have `manager_model()` return the normalized argv alongside the model — but that widens an existing function's contract for one caller; the two-line normalization is sufficient.

### Optional suggestions

- `factory/config.py:288` — `review` accepts any string; spec names only `"escalated"`/`"all"`. A typo (`"escalted"`) is silently stored and only surfaces in T6. One-line `ConfigError` guard matches the existing `manager_model` validation style. Not required: no acceptance criterion demands value validation.
- `factory/config.py:302` — reuse the `manager` local from line 269.

Summary — Standards: 1 judgement-call smell (duplicated lookup), no hard violations. Spec: all criteria satisfied on the array path; 1 required fix (string-form `command` mis-parsed, breaking the doctor "reports the binary" criterion).

VERDICT: REVISE

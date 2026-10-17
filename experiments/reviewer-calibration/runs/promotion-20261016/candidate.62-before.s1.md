## Standards

No repo-level AGENTS.md/CONTRIBUTING.md; `docs/` holds plans only. Standards axis = consistency with existing module patterns + smell baseline.

- **Possible Duplicated Code (judgement call)** — `factory/onboard.py:234` re-implements the omp bare-`{prompt}` rule from `factory/config.py:149`. Two sites now encode "argv[0] is omp ∧ `{prompt}` is an exact element"; they can drift. Optional: in doctor, wrap `cfg.manager_cmd(Path("{prompt}"), cfg.root)` in `try/except ConfigError` and append `str(exc)`. Not blocking.
- Conventions otherwise held: `report(None, …)` is the documented WARN path (`factory/onboard.py:168`); failure text goes through `dispatch.record`; `ConfigError` reused rather than a new exception type; prompt file lands in `cfg.factory` beside escalations.

## Spec (issue #62 + agent brief)

Acceptance mapping:

| Criterion | Evidence | Status |
|---|---|---|
| `manager_cmd(prompt_path, cwd)` expands `{prompt}` with a path; worker shape | `factory/config.py:146-151`; test `tests/test_factory.py:150-165` asserts `@<path>` + readable content | ✓ |
| Prompt written to `<factory>/manager-prompt-<n>.md` | `factory/manage.py:263-264` (spec put the write inside `manager_cmd`, but the spec signature takes a path, so the write can't live there; equivalent) | ✓ |
| Bare `{prompt}` for omp → `ConfigError` naming `@{prompt}`; other programs untouched | `factory/config.py:149-150`; test `:164-165` | ✓ |
| Default manager = worker shape | No `DEFAULT_MANAGER` exists (`config.py:105` defaults to `None`); README `:215-216` and template `:65` examples use `@{prompt}` | ✓ (n/a in code) |
| doctor `manager command` row WARN/FAIL/PASS | `factory/onboard.py:226-238`; test `:410-436` covers all 8 shapes | ✓ |
| Nonzero exit → `Manager command exited N (argv: unexpanded)` + ≤5 stderr lines | `factory/manage.py:267-270`; test `:764-768` | ✓ |
| `escalate` event `reason: manager_failed` | `factory/manage.py:272-273` | ✓ |
| README + template doc `{prompt}` file for workers/manager, text for reviewer | `README.md:36-38,97-105`; `factory/templates/factory.toml:25,51,63-64` | ✓ |

### Changed signatures / defaults / events — consumer check

- `Config.manager_cmd` signature: callers `factory/learn.py:68` and `factory/manage.py:265` updated; no others (grep). Holds.
- New `escalate` rows with `reason=manager_failed`:
  - `factory/manage.py:237-243` — latest-escalate lookup finds the new row, but the `manage` row at `:286` shares its `round`, so no replay. Holds (test `:783-793`).
  - `factory/dispatch.py:375-378` — excluded from round arithmetic. Holds.
  - `factory/stats.py:167` — counted in `escalation_count`; matches "so `factory stats` can count it".
  - `factory/learn.py:39` — ticket already "finished" by the original escalate. Holds.
  - `factory/dispatch.py:245-248` — `record("escalate")` runs inside the `manage` lifecycle scope (`manage.py:225`), so that execution exits with `outcome=project_escalation, reason=manager_failed`. `lifecycle.py:642` maps that to `completed`, not `failed`. Semantically accurate; informational.
- doctor row label `manager: <argv0>` → `manager command`: no consumer outside the updated test (grep). Holds.
- `except (OSError, config.ConfigError)` at `manage.py:278`: `learn.py:68` has no equivalent — a bare-`{prompt}` omp config makes `factory learn` traceback. Same as any `ConfigError` from `config.load` today (`cli.py:45` has no handler). Pre-existing pattern; not blocking.

## Required fixes

None.

## Optional suggestions

1. **Scope creep in template** — `factory/templates/factory.toml:44,48`: the `ci-fix`/`conflict` worker examples lost their instruction text ("declare a flake with evidence", "preserve both intents"). `worker_when` only feeds the manager menu (`factory/manage.py:253`), not the worker, so the inline command text was the only channel for those instructions. The old shape (`omp … "Read the ticket prompt at {prompt}. …"`) is still valid (inline text, not a bare `{prompt}` element). Issue #62 is manager-only; suggest reverting these two lines.
2. **Doc overstates rejection for workers** — `README.md:99-100` and `factory/templates/factory.toml:25` say bare `{prompt}` "is rejected", but only `manager_cmd` rejects (`config.py:149`); `Config.worker` is unvalidated, and omp would run with the path as prompt text. Reword to "rejected for the manager; workers get a useless prompt".
3. **Round budget after manager failure** — a failed manager run records a `manage` row at `round_number` (`manage.py:286`), so with `manager_rounds=1` the manager never sees a real round for that ticket. Test `:752` sidesteps this with `manager_rounds=2`. Not in the issue's criteria; consider excluding `decision=HUMAN`-from-failure rows from the budget if that matters operationally.
4. Duplicated omp check (`onboard.py:234` vs `config.py:149`) — see Standards.

Summary: Standards 1 judgement-call smell; Spec all criteria met, 1 scope-creep note, 1 doc inaccuracy.

VERDICT: APPROVE

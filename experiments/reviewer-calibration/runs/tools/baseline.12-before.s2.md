## Standards

Sources: `README.md` (Configuration / Operating it), `docs/manager-plan.md` §4.3–4.4, T3; `factory/templates/factory.toml`. No `AGENTS.md`/`CONTRIBUTING.md` in repo.

No violations. The diff mirrors the `review` table end to end: `KNOWN_KEYS` entry (`factory/config.py:60`), flat `Config` fields beside `reviewer` (`factory/config.py:104-106`), loader shape identical to `review.command` (`factory/config.py:283-288`), doctor row in the same `report(shutil.which(...))` form as reviewer (`factory/onboard.py:225-227`), dashboard `config` entry joined like `reviewer` (`factory/dashboard.py:839-844`), template comment block in the existing commented-default style (`factory/templates/factory.toml:43-47`). Test lambda with `# noqa: E731` (`tests/test_factory.py:281`) matches the existing pattern at `tests/test_factory.py:234`. `[manager]` already sits in `HOST_TABLES` on `origin/main` (`factory/config.py:50`), so the "host-layer like `review`" requirement holds without a diff hunk.

Judgement call (non-blocking): the new loader lines run *before* the pre-existing `[manager]` validator.
- `factory/config.py:285-288` reads `manager["command"]`/`rounds`/`review`; `factory/config.py:302` calls `manager_model(raw.get("manager", {}))`, which is where `[manager] must be a table` / `manager.command must be an argv array or command string` (`factory/config.py:226-237`) are raised.
- Consequence: `manager = 5` or `command = 5` now dies with a bare `TypeError` at `factory/config.py:285-286` instead of the existing `ConfigError`; `command = "omp -p"` (string form `manager_model` explicitly accepts at `factory/config.py:231-233`) is silently char-split by `list(...)` at `factory/config.py:286`, so doctor would print `manager: o`.
- Fix is a reorder: move `285-288` below `302`, or normalise `command` through the same `shlex.split` path. Not a documented-convention breach; the issue specifies the argv form only.

## Spec

Issue #12 (`docs/manager-plan.md` T3). All exit-gate items met:

- `command` argv template, unset = disabled: `factory/config.py:104`, `285-286`; template `factory/templates/factory.toml:44-46`.
- `rounds = 1` default: `factory/config.py:105`, `287`; template `:47`.
- `review = "escalated"`: `factory/config.py:106`, `288`; template `:48` names `"all"`.
- Host-layer table (`HOST_TABLES`): already present on base, `factory/config.py:50`.
- `factory doctor` prints `manager:` only when configured: `factory/onboard.py:226-227`; tested both ways `tests/test_factory.py:276-284`.
- Unknown `[manager]` keys reported as drift: `tests/test_factory.py:252`, `:263`.
- `dashboard --json` `config` includes the table: `factory/dashboard.py:840-844`.
- Loader test covering all three keys: `tests/test_factory.py:112-115`, `:137-138`.
- Out of scope respected: nothing in the diff executes `cfg.manager`.

Non-blocking gap: `review` is documented as a closed set (`"escalated"`, `"all"` allowed) but the loader accepts any string (`factory/config.py:288`). Not in the exit gate; T6 (the consumer) can reject unknown values when it lands.

Summary: Standards 1 judgement-call finding (validator ordering, `factory/config.py:285-288` vs `:302`); Spec 0 failures, 1 note (`review` value unvalidated). Neither fails the issue's acceptance criteria or a documented convention.

VERDICT: APPROVE

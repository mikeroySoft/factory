# Review: issue #20 — when-rules, FIX, ci-fix/conflict profiles

## Standards (README.md; no AGENTS.md/CONTRIBUTING.md at this head)

- README manager section updated for `FIX`, table-form workers, and `when` listing (`README.md:88-102`). Consistent with `MENU` text in `factory/manage.py:19-29`.
- Template documents the table form and keeps array form (`factory/templates/factory.toml:31-47`). Matches README's "legacy argv array or `[workers.<label>]` table".
- Single config source preserved: `when` parsed only in `factory/config.py:288-298`, exposed via `Config.worker_when` (`factory/config.py:103`), read by `manage.py:200-201` and `dashboard.py:847`.

No convention violations found.

## Spec (issue #20 + agent brief)

| Criterion | Evidence | Status |
|---|---|---|
| `[workers.<label>]` may carry `when` | `factory/config.py:288-298`; test `tests/test_factory.py:147-165` | met |
| Prompt lists labels with `when` | `factory/manage.py:199-201`; asserted `tests/test_factory.py:672` | met |
| ROUTE only for listed labels | `factory/manage.py:54` (pre-existing check now via `RESERVED_LABELS`); `manage.py:214` passes filtered `workers` | met |
| FIX only for listed labels | `factory/manage.py:63-69`; test `tests/test_factory.py:738-748` covers `ci-fix` unconfigured, `default`, reserved label, non-string | met |
| ROUTE chore w/o chore worker rejected and recorded | parse falls through to HUMAN (`manage.py:70-72`); `record("manage", decision=...)` precedes `apply` (`manage.py:219`); test `tests/test_factory.py:669-676` | met |
| ROUTE chore with worker applies label | test asserts `--add-label chore` (`tests/test_factory.py:673`) | met |
| FIX on red CI runs `ci-fix` argv | `apply` FIX branch `manage.py:104-131`; test `tests/test_factory.py:678-736` checks worker ran, guidance+packet delivered, push only on gate PASS, `factory-approved` only on fresh APPROVE | met |
| Array-form `[workers]` still loads | `config.py:290` treats non-dict entry as argv; test `tests/test_factory.py:157` | met |
| Two default profiles in template | `factory/templates/factory.toml:41-47` | met (as commented, opt-in blocks — see note below) |
| Manager does not write host config | FIX path touches only worktree/PR/labels (`manage.py:104-131`) | met |

## Required fixes

None.

## Optional suggestions

1. **"Ship two default profiles" delivered as commented-out blocks** (`factory/templates/factory.toml:41-47`). Defensible under "remain host-config, human-applied", but on a fresh `factory init` a `DECISION: FIX {"worker":"ci-fix"}` is rejected until a human uncomments/copies them. Issue text is ambiguous; flagging the interpretation, not demanding a change.
2. **Template comment points at host-config layer but lives in repo-config template** (`factory/templates/factory.toml:37-39`): "copy these into the host config under `[defaults.workers.<label>]`" while the surrounding file is `.factory.toml`. Uncommenting in place also works per `config.py:288-298`; consider saying so to avoid implying host config is the only option.
3. **FIX failure re-escalates** (`manage.py:120-121`, `manage.py:130`): `dispatch.escalate` on a ticket already in `ready-for-human`. `[INFERENCE]` If `escalate` writes a fresh packet, the manager may treat it as a new escalation and consume another `[manager].rounds` cycle on the same ticket (FIX → fail → escalate → FIX …). Tests only check a single pass. Worth confirming `rounds` accounting bounds this; if it does, no change needed.
4. **FIX does not verify CI is actually red** (`manage.py:106-111` checks worktree/PR state only). The issue only requires that FIX *on red CI* runs the argv, so this is in-spec; noting that a FIX on a green-CI PR will also run a worker round.
5. `parse` re-subtracts `RESERVED_LABELS` (`manage.py:54`) from a `workers` dict already filtered at `manage.py:199`. Harmless redundancy; keeps `parse` safe for direct callers.

No net-new abstractions beyond the brief; `RESERVED_LABELS` (`manage.py:32`) replaces an inline set literal used in two places.

VERDICT: APPROVE

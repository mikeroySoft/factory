## Standards (README conventions; no AGENTS.md/CONTRIBUTING.md at this head)

- README updated in step with the new decision (`README.md:88-102`); `[workers.<label>]` table form, `when`, FIX semantics, and the opt-in profiles are all documented. Matches the "template written by `factory init` documents them all" convention via `factory/templates/factory.toml:32-47`.
- `config.py` remains the single source of the new value (`factory/config.py:103`, `:288-298`); `manage.py` and `dashboard.py` read `cfg.worker_when` rather than re-parsing TOML (`factory/manage.py:201-202`, `factory/dashboard.py:847`).
- Manage event is still recorded before any GitHub mutation (`factory/manage.py:219` precedes `apply(...)` at `:221`), consistent with README's "records a `manage` event before GitHub mutations".

No convention violations found.

## Spec (issue #20)

| Criterion | Evidence |
|---|---|
| `when` on `[workers.<label>]`, array form still loads | `factory/config.py:288-298`; test `tests/test_factory.py:147-165` covers table+array coexistence and malformed entries |
| Prompt lists labels with `when` | `factory/manage.py:200-202` (`- {label}: {when or '(no when rule)'}`), asserted at `tests/test_factory.py:672` |
| ROUTE/FIX only for listed labels | ROUTE: `factory/manage.py:54-57`; FIX: `:63-66` (rejects non-str, unlisted, reserved). `parse` also receives the pre-filtered dict (`:213`) |
| `ROUTE chore` without `chore` worker → rejected + recorded | `tests/test_factory.py:669-675`: decision `HUMAN` in `events.jsonl`, no `issue edit` |
| with `chore` worker → label applied | `tests/test_factory.py:673` asserts `--add-label chore` |
| FIX on red CI runs `ci-fix` argv | `factory/manage.py:104-131`; `tests/test_factory.py:677-717` verifies worker ran in `wt-7`, received guidance + packet, pushed only on gate PASS, `factory-approved` only on fresh APPROVE |
| `ci-fix`/`conflict` profiles in template | `factory/templates/factory.toml:40-47` |
| Out of scope: manager writing host config | Not touched; README/template state it explicitly (`README.md:102`, `factory/templates/factory.toml:35`) |

Observations (non-blocking):

- Profiles ship commented out (`factory/templates/factory.toml:40-47`). Consistent with "New profiles remain host-config, human-applied", but a host that wants FIX must uncomment/copy them; `FIX` with `ci-fix` on a repo without that worker falls to `HUMAN` (`factory/manage.py:65`, `tests/test_factory.py:720-729`). Acceptable per issue text.
- `factory/manage.py:55` still subtracts `RESERVED_LABELS` from an already-filtered dict (`:200`, `:213`). Redundant, harmless.
- FIX preconditions failing (no worktree/PR, wrong branch) raise `ValueError` at `factory/manage.py:109`/`:111` after the `manage` event is recorded, landing in the existing `mechanism_failure` path (`:222-224`). The round is consumed, not replayed; matches README's stated non-replay behaviour.

VERDICT: APPROVE

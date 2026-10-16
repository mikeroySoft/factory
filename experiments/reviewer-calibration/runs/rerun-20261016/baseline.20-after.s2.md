## Spec (issue #20)

| Criterion | Evidence | Status |
|---|---|---|
| `[workers.<label>]` table with `command` + optional `when`; array form still loads | `factory/config.py:288-298`; `tests/test_factory.py:147-165` covers both forms plus malformed entries | met |
| Manager prompt lists labels with `when` | `factory/manage.py:199-201` (`- {label}: {when or '(no when rule)'}`), asserted at `tests/test_factory.py:672` | met |
| ROUTE/FIX only for listed labels | ROUTE: `factory/manage.py:54` (`set(workers) - RESERVED_LABELS`); FIX: `factory/manage.py:63-66`; `parse` now receives the filtered map at `factory/manage.py:213` | met |
| ROUTE chore rejected+recorded without worker, label applied with one | `tests/test_factory.py:652-676` asserts `manage` event decision `HUMAN` vs `ROUTE`, `--add-label chore` only when configured | met |
| FIX on red CI runs the selected argv, re-gates, pushes only on PASS, re-reviews | `factory/manage.py:104-131`; `tests/test_factory.py:678-734` (4 cases incl. gate fail → no push, REVISE → no approve, rebase via `conflict`) | met |
| `ci-fix` / `conflict` profiles in template | `factory/templates/factory.toml:36-47` | met, see note |
| `dashboard.py` touched | `factory/dashboard.py:847` exposes `worker_when` | met |
| Out of scope: manager writing host config | no config writes in diff; MENU/README state it (`factory/manage.py:26-28`, `README.md:102-103`) | met |

**Note (non-blocking):** the two profiles ship commented-out (`factory/templates/factory.toml:41-47`) with copy-to-host-config instructions. The issue title says "default profiles", but the triage brief (authoritative interpretation) states "these remain host-config that humans apply", and the README change at `README.md:101-103` documents exactly that. Consistent with the brief; flagging only because a fresh `factory init` repo will reject `FIX ci-fix` until a human applies the profile.

## Standards (README conventions)

- README updated for the new decision and worker forms (`README.md:88-103`); matches code behavior: push gated on `ok` (`factory/manage.py:121-124`), approval only on fresh `APPROVE` (`factory/manage.py:126-129`), no merge/requeue.
- Human veto preserved: `reviewDecision == "CHANGES_REQUESTED"` blocks FIX (`factory/manage.py:108`), consistent with README §6 "A human blocks any merge by requesting changes".
- `manage` event recorded before mutation unchanged (`factory/manage.py:219`); FIX errors fall into the existing `mechanism_failure` path (`factory/manage.py:221-224`).
- Config stays the single source of worker values (`factory/config.py:103`, `:285-298`); no second convention introduced.

No findings that fail acceptance criteria or documented conventions.

VERDICT: APPROVE

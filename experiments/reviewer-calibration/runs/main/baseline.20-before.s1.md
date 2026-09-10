# Review — issue #20, head `8dd6588`

## Spec

| Criterion | Evidence | Status |
|---|---|---|
| `[workers.<label>]` table with optional `when`; array form still loads | `factory/config.py:289-298` — dict entries take `command`/`when`, non-dict entries pass through as argv; `tests/test_factory.py:147-165` covers both forms and rejects malformed entries | met |
| Manager prompt lists labels with `when` | `factory/manage.py:199-201` — reserved labels filtered, each remaining label rendered as `- <label>: <when or '(no when rule)'>` | met |
| `ROUTE` only for listed labels; rejected + recorded otherwise | `factory/manage.py:54` (ROUTE set excludes `RESERVED_LABELS`), `:213` passes the filtered `workers` to `parse`; unknown label falls through to `HUMAN` and `:220` records the `manage` event before `apply`. `tests/test_factory.py:652-676` asserts HUMAN/no edit without a `chore` worker and `--add-label chore` with one | met |
| `FIX` only for listed labels | `factory/manage.py:63-69` rejects non-string, unlisted, or reserved workers and empty guidance; `tests/test_factory.py:722-732` covers unconfigured, `default`, `ready-for-agent`, non-string | met |
| `FIX` on red CI runs the selected argv | `factory/manage.py:104-131` — verifies kept worktree, open `agent/<n>` PR, no CHANGES_REQUESTED, correct branch; runs one `worker_round` with only `{data["worker"]}` and guidance+packet; pushes only on gate PASS; re-reviews; APPROVE → `approve_pr`, else escalate. `tests/test_factory.py:678-720` exercises gate-fail, REVISE, APPROVE matrix | met |
| `ci-fix` and `conflict` profiles in template | `factory/templates/factory.toml:41-47` | met (see note) |
| Dashboard exposes `when` | `factory/dashboard.py:847` | met |

**Note (not blocking):** the issue says "Ship two default profiles"; the diff ships them commented out (`factory/templates/factory.toml:41-47`) with instructions to copy into host config (`:37-39`). This matches the issue's "remain host-config, human-applied" clause and the triage brief ("these remain host-config that humans apply"), and the README text at `README.md:101-102` documents it as opt-in. If "default" was intended to mean active-by-default in the repo template, this is a scope question for the issue author, not a code defect.

## Standards

- README manager paragraph updated for `FIX`, table-form workers, and `when` (`README.md:88-102`), consistent with the existing "documented in README" convention for manager decisions.
- `RESERVED_LABELS` (`factory/manage.py:32`) replaces the inline set previously at the ROUTE branch (`:54`); single definition, both ROUTE and FIX use it.
- Test additions follow existing `scenario()`/`stub_bin` patterns and assert observable effects (labels applied, push occurred, worker input contents), not internals.

No findings that fail acceptance criteria or documented conventions.

VERDICT: APPROVE

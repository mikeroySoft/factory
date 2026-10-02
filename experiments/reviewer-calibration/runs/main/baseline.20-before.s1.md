## Spec (issue #20)

| Criterion | Evidence | Status |
|---|---|---|
| `[workers.<label>]` table with optional `when` | `factory/config.py:288-298` parses dict-or-list entries, validates argv and `when` type, stores `worker_when` | met |
| Array-form `[workers]` still loads | `factory/config.py:290-291` (`command = entry` when not dict); `tests/test_factory.py:147-157` | met |
| Manager prompt lists labels with `when` | `factory/manage.py:199-201` emits `- label: <when or "(no when rule)">` for non-reserved labels | met |
| ROUTE/FIX only for listed labels | `factory/manage.py:54`, `:63-66`; `parse()` now receives the filtered `workers` dict at `:213` | met |
| `ROUTE chore` without `chore` worker rejected and recorded | `tests/test_factory.py:652-676` asserts `manage` event decision `HUMAN` and no `issue edit`; with worker, `--add-label chore` | met |
| `FIX` on red CI runs the `ci-fix` argv | `factory/manage.py:104-130` runs one `worker_round` with `{data["worker"]}`, re-gates, pushes only on PASS, re-reviews, approves only on fresh APPROVE; `tests/test_factory.py:678-717` | met |
| `ci-fix` / `conflict` profiles in template | `factory/templates/factory.toml:41-47` | met, with caveat below |
| `dashboard.py` touched | `factory/dashboard.py:847` exposes `worker_when` | met |
| Out of scope: manager writing host config | no config writes in `apply`; `factory/manage.py:104-130` | respected |

### Caveat (non-blocking)

`factory/templates/factory.toml:41-47` ships the two profiles commented out, as copy-into-host-config instructions. The issue says "Ship two default profiles in `templates/factory.toml`" but immediately qualifies "New profiles remain host-config, human-applied", and the triage brief repeats "these remain host-config that humans apply." The exit gate exercises `ci-fix` only when configured (`tests/test_factory.py:685`), so an opt-in template satisfies the acceptance criteria. Flagging only because "ship ... default profiles" could be read as active-by-default; the diff chose the conservative reading consistent with the out-of-scope clause.

## Standards (README at head)

- README updated for the new `FIX` decision and table-form workers (`README.md:88-101`), matching the `MENU` text at `factory/manage.py:19-29`. Consistent.
- `config.py` remains the single source of worker/`when` values (`factory/config.py:103`, `:288-298`); `manage.py` and `dashboard.py` read from `cfg` only. Matches the "config.py as the single source" architecture note.
- `manage` event recorded before mutations (`factory/manage.py:219`, unchanged ordering), per README's "records a `manage` event before GitHub mutations".
- Tests follow the existing `scenario()`/`stub_bin` conventions (`tests/test_factory.py:655`, `:690-702`).

No findings that fail acceptance criteria or documented conventions.

VERDICT: APPROVE

## Standards

README (`README.md:85-102` in diff) is the only convention source at this head; the diff updates it for FIX, table-form workers, and the opt-in profiles. `factory/manage.py:217-219`: `manage` event is still recorded before `apply`, so the documented "records a `manage` event before GitHub mutations" rule holds for FIX too. No convention violations found.

## Spec (issue #20 / brief)

| Criterion | Evidence | Status |
|---|---|---|
| `when` on `[workers.<label>]`, array form still loads | `factory/config.py:288-298` handles dict (`command`/`when`) or list per label; `tests/test_factory.py:147-165` covers both plus malformed entries | Met |
| Prompt lists labels with `when`; ROUTE/FIX only for listed labels | `factory/manage.py:199-201` builds filtered `workers` and prints `- label: when`; `factory/manage.py:213` passes the filtered dict to `parse`; `factory/manage.py:63-69` FIX validation; `tests/test_factory.py:652-676, 718-729` | Met |
| ROUTE chore rejected+recorded without a chore worker; label applied with one | `tests/test_factory.py:669-676` asserts `HUMAN` manage event / `--add-label chore` | Met |
| FIX on red CI runs `ci-fix` argv | `factory/manage.py:104-131` dispatches `worker_round(n, wt, {data["worker"]}, ...)`, re-gates, pushes only on PASS, re-reviews, approves only on APPROVE; `tests/test_factory.py:678-716` | Met |
| Two profiles in `templates/factory.toml`, human-applied | `factory/templates/factory.toml:31-48` (`ci-fix`, `conflict`, commented as opt-in host config) | Met — consistent with "remain host-config, human-applied" |
| `dashboard.py` touched | `factory/dashboard.py:847` exposes `worker_when` in snapshot | Met |
| Out of scope: manager writing host config | No config writes in FIX path | Respected |

## Required fixes

None.

## Optional suggestions

- `factory/manage.py:107-109`: `pr["state"]` / `pr["headRefName"]` / `pr["reviewDecision"]` use indexing; a `KeyError` is not in the `(CalledProcessError, OSError, ValueError)` set caught at `factory/manage.py:222`, so a malformed `gh pr view` payload would abort the whole `manage` pass rather than failing that one ticket. `.get(...)` would route it into the existing `ValueError` message. `[INFERENCE]` `gh --json` with explicit fields normally returns all keys, so trigger is unlikely.
- `factory/manage.py:54`: `set(workers) - RESERVED_LABELS` is now redundant since `manage_pass` already filters (`factory/manage.py:199`). Harmless defense in depth; mention only.
- `factory/dashboard.py:847`: `worker_when` is added to the JSON snapshot but nothing in the diff renders it in the UI. Not required by the issue (which names the file but no acceptance criterion); noting so it isn't mistaken for a UI change.

No net-new abstractions beyond the brief; `RESERVED_LABELS` (`factory/manage.py:32`) is a constant extracted from an existing inline set and reused in three places.

VERDICT: APPROVE

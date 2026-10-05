# Review: issue #20 — when-rules, FIX, ci-fix/conflict profiles

## Spec coverage

| Criterion | Evidence | Status |
|---|---|---|
| `when` on `[workers.<label>]`, table-or-array | `factory/config.py:288-298` parses dict (`command`/`when`) or legacy list; `tests/test_factory.py:147-165` covers both forms plus malformed entries | Met |
| Prompt lists labels with `when` | `factory/manage.py:199-201` emits `- label: when or (no when rule)`; asserted at `tests/test_factory.py:672` | Met |
| ROUTE/FIX only for listed labels | `factory/manage.py:33`, `:54`, `:63-69`; `parse` now receives the filtered map (`:213`); `test_fix_rejects_unlisted_workers` and the `configured=False` ROUTE subtest confirm rejection recorded as `HUMAN` | Met |
| ROUTE chore without worker rejected and recorded; with one, label applied | `tests/test_factory.py:652-675` asserts `manage` event decision and `--add-label chore` | Met |
| FIX on red CI runs the selected argv, re-gates, pushes only on PASS, re-reviews | `factory/manage.py:104-131`; `tests/test_factory.py:677-732` covers gate fail / REVISE / APPROVE / rebase | Met |
| Two profiles in template, human-applied | `factory/templates/factory.toml:31-47` (commented opt-in blocks) | Met |
| Manager cannot write host config | FIX only runs an existing worker; no config mutation path | Met |
| Touches limited to listed files + README/tests | diff file list | Met |

## Required fixes

None.

## Optional suggestions (non-blocking)

1. `factory/manage.py:54` — `set(workers) - RESERVED_LABELS` is now redundant: `manage_pass` already passes the filtered map (`:197`, `:213`). Harmless; keeps `parse` safe if called with `cfg.workers` directly. Leave or drop — either is fine.
2. `factory/manage.py:112-114` — attempt number is `max(existing attempts)+1`. README's event contract reserves `max_attempts + bounce` numbering for review bounces; a FIX round after bounces could reuse a bounce-range number in `.factory/logs/<n>-attempt-<k>.log`. Not an acceptance criterion; note it if log-name collisions matter.
3. `factory/manage.py:123-131` — after a successful FIX gate the new report is only given to the reviewer; the PR body's gate report (which the merge stage reads for "gate PASS in the PR body", README step 6) is not refreshed. Merge still proceeds because the prior body said PASS, but the evidence of record is stale. `[INFERENCE]` on how `dispatch.review`/`pr_comment` handle the body; not a blocker.
4. `factory/templates/factory.toml:38` — the template comment points to `[defaults.workers.<label>]`/`[repo."owner/name".workers.<label>]`. Whether the host-config merge feeds the new table-form parser is outside this diff; `[INFERENCE]` that the existing merge produces the same `workers` mapping. Worth a one-line host-config load test if that layer is not already covered.

No net-new abstractions beyond the brief; `RESERVED_LABELS` (`factory/manage.py:33`) just deduplicates an existing literal set.

VERDICT: APPROVE

## Standards

No `AGENTS.md`/`CONTRIBUTING.md` in the snapshot; documented sources are `README.md` and `docs/manager-plan.md` (T11, lines 341–352). README updated in-diff (`review.diff:10-24`) and matches the code paths it describes (gate-PASS-only push `manage.py:121-124`, fresh-APPROVE-only relabel `:127-128`, no requeue `:131`). Config validation follows the existing `ConfigError` pattern (`review.diff:49-52`). `RESERVED_LABELS` extraction (`review.diff:102,112,122`) removes a duplicate set literal rather than adding abstraction. No smell-baseline findings beyond judgement calls below.

## Spec (issue #20 / brief comment)

| Criterion | Evidence | Status |
|---|---|---|
| `[workers.<label>]` may carry `when`; array form still loads | `review.diff:45-55`, test `:236-254` | met |
| Manager prompt lists labels with `when` | `review.diff:176-178`, test `:283` | met |
| ROUTE/FIX only for listed labels | `review.diff:111-115,120-126`, tests `:263-286,:332-342` | met |
| Ship `ci-fix` and `conflict` profiles in template; human-applied | `review.diff:218-224` | met |
| ROUTE chore w/o worker rejected+recorded; with worker label applied | test `:281-286` (HUMAN decision in `manage` event; `--add-label chore`) | met |
| FIX on red CI runs `ci-fix` argv | `review.diff:152-155`, test `:303-330` | met |
| Out of scope: manager writes host config | nothing in diff writes config | met |

### Required fixes

1. **FIX push cannot succeed for the shipped `conflict` profile.** `manage.py` (`review.diff:159`) runs `git push origin agent/{n}` (plain). The `conflict` profile this diff ships (`review.diff:222-224`) instructs the worker to restart and resolve the rebase; the dispatcher's conflict escalation aborts the rebase first (`snapshot/factory/dispatch.py:722-724`), so a successful `conflict` worker always rewrites `agent/{n}` history. Plain push is rejected non-fast-forward → `CalledProcessError` → caught at `review.diff:197-199` as `mechanism_failure` with only a local log line; no GitHub comment, no new escalation, and with `manager_rounds=1` the round is consumed (`manage.py:189-192`), so the resolved rebase sits unpushed and invisible. The repo's own post-rebase push uses `--force-with-lease` (`dispatch.py:731`). Fix: `["git", "push", "--force-with-lease", "origin", f"agent/{n}"]` at `review.diff:159`; update the assertion at `review.diff:330` accordingly. Rule source: issue scope "`conflict` (resolve the rebase, keep both intents…)" + plan T11 line 142 ("Dispatch a worker round (`ci-fix`, `conflict`…)") — the diff ships a profile its own FIX path cannot complete. The gate's test PASS doesn't cover it: the test stubs `git push` to `exit 0` (`review.diff:316`).

### Optional suggestions

- `KNOWN_KEYS["workers"] = None` (`snapshot/factory/config.py:58`) means `factory doctor` won't flag a mistyped `whne =` in a `[workers.<label>]` table; the key is silently dropped at `review.diff:48`. Consider validating table keys ⊆ `{command, when}` in the loader.
- `dashboard.py` exposes `worker_when` (`review.diff:67`) but `dashboard.html:894` doesn't render it. Harmless; wire it or drop it.
- `pr checks` stub in the FIX test (`review.diff:313`) is unused by the code path.

## Plan drift

Plan status: not recorded — no planned file list or proof to diff against. Files changed vs. issue "Touches": `README.md` and `tests/test_factory.py` are additional; both are justified (docs for new decision; tests are the exit gate). Gate report covers the stated verification (`uv run pytest tests/test_factory.py` → test PASS). No proof gap exposes a defect; the required fix above is a code defect, not drift.

VERDICT: REVISE

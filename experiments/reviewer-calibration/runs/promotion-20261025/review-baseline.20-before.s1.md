## Standards

Sources: `README.md`, `docs/manager-plan.md` (T11, §4.3 authority boundary), `factory/templates/factory.toml`. No `AGENTS.md`/`CONTRIBUTING.md` in the snapshot; ruff `line-length = 100` exists in `pyproject.toml` but is not run by the gate or CI, and long lines pre-exist throughout `manage.py`/`config.py` — not reported.

- Authority boundary (`docs/manager-plan.md:142-143`: FIX = one worker round, profile chosen by `[workers].when`; re-add `factory-approved` only after gate PASS + fresh APPROVE) — honored: `review.diff:152-165` runs exactly one `worker_round`, escalates on gate FAIL before pushing, pushes, re-reviews, `approve_pr` only on `APPROVE`.
- "Manager may not write host config" (`docs/manager-plan.md:352`) — honored: `review.diff:120-126` only accepts labels already in `workers`; template profiles are comments the human copies (`review.diff:214-224`).
- Code-before-mutation audit row (`README.md`: "records a `manage` event before GitHub mutations") — unchanged: `review.diff:193-196` records then applies.
- README updated in the same diff (`review.diff:9-24`) consistent with the implementation.
- Smell baseline: `RESERVED_LABELS` (`review.diff:102`) replaces an inline set already used at `review.diff:111`; justified dedup, not speculative. `parse` re-filters a dict `manage_pass` already filtered (`review.diff:112,122` vs `review.diff:176`) — redundant but harmless (judgement call, not blocking).

## Spec (issue #20 + triage brief)

| Criterion | Evidence | Status |
|---|---|---|
| `[workers.<label>]` may carry `when` | `review.diff:45-55` | ✓ |
| Array form still loads | `review.diff:47-48` (non-dict entry passthrough), test `review.diff:236-248` mixes array `default` with table `chore` | ✓ |
| Manager prompt lists labels with `when` | `review.diff:176-178`; asserted at `review.diff:283` | ✓ |
| ROUTE only for listed labels; unlisted rejected and recorded | `review.diff:111-115`; test `review.diff:263-286` (HUMAN decision recorded as `manage` event, no `issue edit`) | ✓ |
| ROUTE with configured worker applies label | `review.diff:284` | ✓ |
| FIX only for listed labels | `review.diff:120-123`; test `review.diff:332-342` (unconfigured `ci-fix`, `default`, `ready-for-agent`, non-string) | ✓ |
| FIX on red CI runs the `ci-fix` argv | `review.diff:152-155` selects `{data["worker"]}` → `Config.worker` first-match; test `review.diff:317,321-324` proves the `fix-worker` argv ran with guidance + packet | ✓ |
| Ship `ci-fix` and `conflict` profiles in template | `review.diff:218-224` | ✓ |
| `dashboard.py` touched | `review.diff:67` | ✓ |
| Out of scope: manager writing host config | not implemented | ✓ |

No scope creep beyond the brief. The FIX preconditions (`review.diff:144-147`: open PR on `agent/<n>`, worktree on branch, no `CHANGES_REQUESTED`) are guards, not new abstractions.

## Plan drift

Plan status not recorded; comparing against the issue's "Touches" list.

- Files changed not named by the issue: `README.md` (doc sync for the new decision/config shape), `tests/test_factory.py` (exit-gate proof). Both are expected companions; neither exposes a defect.
- Planned proof: issue names `uv run pytest tests/test_factory.py`; gate ran `python -m unittest discover -s tests` (`.factory.toml:4-13`) → `test: PASS`. Same test file, equivalent proof. All four exit-gate scenarios have a corresponding test (table above). Nothing missing.

## Required fixes

None.

## Optional suggestions

- `review.diff:67` adds `worker_when` to the dashboard snapshot but `dashboard.html:893-894` still renders only `cfg.workers`; the `when` rules are not visible in the UI. Issue only names `dashboard.py`, so not required.
- `review.diff:139-166`: every other decision posts a `Factory manager: …` issue comment; FIX leaves the issue (still `ready-for-human`) with no trace except the PR review comment and the `approved`/`escalate` events. A one-line issue comment would keep the human's inbox consistent. No documented rule requires it.
- `review.diff:49` / `review.diff:144`: 140–150-char lines exceed `pyproject.toml` ruff `line-length = 100`; unenforced by gate/CI and consistent with surrounding code, so informational only.

VERDICT: APPROVE

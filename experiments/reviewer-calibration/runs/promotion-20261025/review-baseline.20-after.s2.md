## Standards

Snapshot has no `AGENTS.md`/`CONTRIBUTING.md`; documented sources are `README.md`, `docs/manager-plan.md` §4.4, `pyproject.toml`, and the template's own conventions.

- **Decide → record → act** (plan §4.4): `manage` event recorded before any GitHub mutation — `manage.py:193` precedes `apply(...)` at `manage.py:196`. OK.
- **Closed decision menu applied by code** (§4.4): FIX `worker` is validated against configured, non-reserved labels (`manage.py:120-125`); `RESERVED_LABELS` (`manage.py:102`) also excludes `default`/`ready-for-agent`/`ready-for-human`/`needs-triage` from the prompt list (`manage.py:176`). OK.
- **Loop safety** (§4.4 `manager_rounds`): FIX failure routes through `dispatch.escalate` (`manage.py:157,165`), which bumps the escalation round; `manage_pass` still gates on `1 <= round <= manager_rounds`. Bounded. OK.
- **Template convention** ("defaults are shown commented out"): `ci-fix`/`conflict` profiles shipped commented (`factory.toml:218-224`), consistent; a live `[workers.ci-fix]` table without `default` would fail `config.py:42-43`, so this is the only loadable form.
- `pyproject.toml` declares `ruff line-length = 100` but no gate/CI step runs ruff and pre-existing lines already exceed it (`manage.py:114` context). Not enforced → not a finding.

## Spec (issue #20 + triage brief)

| Criterion | Evidence | Status |
|---|---|---|
| `[workers.<label>]` may carry `when` | `config.py:36,45-55` | Met |
| Manager prompt lists labels with `when` | `manage.py:176-178`; test `test_factory.py:283` | Met |
| ROUTE/FIX only for listed labels | `manage.py:111-115,120-123`; tests `test_factory.py:263-286,349-359` | Met |
| Two profiles in `templates/factory.toml` | `factory.toml:218-224` | Met |
| Existing array-form still loads | `config.py:47` (non-dict entry path); test `test_factory.py:240,247` | Met |
| ROUTE chore without worker rejected+recorded; with worker label applied | `test_factory.py:281-286` | Met |
| FIX on red CI runs the selected argv | `manage.py:152-155` (`{data["worker"]}` as labels → `cfg.worker` first-match); test `test_factory.py:326-338` | Met |
| Out of scope: manager writing host config | No config writes in diff | Met |

FIX itself wasn't in base (T10/#19 not landed), so the diff implements it per plan T10 (`manager-plan.md:283-285`: one `worker_round` with guidance as `extra`, re-gate, push, re-review, approve only on fresh APPROVE). `manage.py:139-166` mirrors `process_ticket`'s existing review/push/approve sequence (`dispatch.py:1056-1071`). `--force-with-lease` (`manage.py:159`) is needed for the `conflict` rebase case and fails closed if a human pushed the branch.

## Plan drift

Plan status not recorded; nothing to diff against. Files beyond the issue's "Touches": `README.md` (doc update for new decision/config shape) and `tests/test_factory.py` (exit-gate tests). Neither exposes a defect. Gate `test: PASS` covers the issue's `pytest tests/test_factory.py` proof (same suite via `unittest discover`).

## Required fixes

None.

## Optional suggestions

1. `manage.py:139-166` — FIX is the only decision that posts no `Factory manager:` comment on the issue (REWRITE/SPLIT/ROUTE/HUMAN do at `manage.py:134-157` of the snapshot). On success the human sees only a reviewer PR comment and a label; the FIX guidance is visible only in `events.jsonl`/worker log. A one-line `gh("comment", "--body", "Factory manager: FIX via <worker>: <guidance>")` before `worker_round` keeps attribution consistent with plan §4.4 (`manager-plan.md:176-177`).
2. `manage.py:144-147` — pre-flight `ValueError`s (no worktree, PR closed, CHANGES_REQUESTED) surface as `mechanism_failure` with a consumed manager round and no GitHub trace. Treating them as a HUMAN diagnosis (comment + leave) would match §4.4 "exhausted → diagnosis attached". Same ceiling as existing SPLIT/REWRITE failures, so non-blocking.
3. `manage.py:160-163` — `human_activity` is checked before `apply` but not after the (long) worker round before `approve_pr`. Branch pushes are protected by the lease; label/comment takeover during the run is not. Non-blocking; same window exists in `process_ticket`.
4. `manage.py:149-150` re-reads `events.jsonl` that `manage_pass` already loaded (`manage.py:184` in snapshot). Trivial.

VERDICT: APPROVE

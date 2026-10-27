## Standards

Sources checked: `README.md`, `CHANGELOG.md`, `pyproject.toml`, `.github/workflows/ci.yml`, `docs/*.md`. No `AGENTS.md`/`CONTRIBUTING.md` in the snapshot.

- No documented-rule violation found. The only lint-ish config is `[tool.ruff] line-length = 100` (`pyproject.toml:36-37`); `review.diff:167` is 104 cols, but CI (`ci.yml:12`) runs only `unittest`, and `snapshot/factory/dashboard.py` already has 28 lines >100 cols, so this is not an enforced rule. Optional.
- Baseline smell, judgement call — **Duplicated Code**: `review.diff:181-191` re-derives `review_pending/changes_requested/ci_pending/ci_failed/ready` from `readiness.checks`, while dispatch already records that classification in the same event (`snapshot/factory/dispatch.py:300-312`, `state=`). The recomputation is only needed for the window where a `review-result` exists for the head but no `review-readiness` yet. Optional.

## Spec (issue #9 + agent brief comment)

Requirements vs diff:

| Criterion | Evidence | Status |
|---|---|---|
| Snapshot data: number, title, author, head SHA, last reviewed SHA, verdict, CI state, one of six states | `review.diff:194-200` | Met |
| Six states | `review.diff:185-193`; test asserts all six `review.diff:286-288` | Met |
| Opted-in PRs (label, review request to viewer, or prior admitted result; consumed requests retained) | `review.diff:165-174`, mirrors `dispatch.py:211-223`; test `review.diff:252-258, 273-274` | Met |
| Old approval never makes new head ready | `review.diff:176-177` binds `current`/`readiness` to `head`; test PR 1 (`review.diff:261-263`, `294-296`) | Met |
| Rows link to GitHub | `review.diff:64`; test `review.diff:290` | Met |
| Stalled items in inbox, no mutation controls | `review.diff:84-90`, `115-116` (GitHub pointer text only) | Met |
| README operating description | `review.diff:10-26` | Met |
| Snapshot test covering six states + links + SHA-specific data | `review.diff:238-300` | Met |
| No mutation of labels/reviews/branches/merge | `review_queue` is pure; GraphQL read only (`review.diff:136,144-145`); events file unchanged asserted `review.diff:284` | Met (test-side); runtime proof not in gate report |
| Out of scope (submitting reviews, fixing branches, merging, separate service) | none added | Respected |

Observations (non-blocking):
- `review.diff:86`: `pr.state === 'review_pending' && pr.reason` is unreachable — `reason` comes only from `escalate` (`review.diff:199`; `review-result` rows carry no `reason`, `dispatch.py:363`), and any escalation forces `state = "escalated"` (`review.diff:192-193`). Dead condition; harmless.
- `review.diff:156` (`labels(first:10)`) and the pre-existing `pullRequests(first:100, CREATED_AT DESC)` (`snapshot/factory/dashboard.py:150`) bound the queue: an opted-in PR older than the 100 most recent PRs, or with >10 labels and no review request/prior result, is omitted here but admitted by dispatch (`gh pr list --limit 1000`, `dispatch.py:203-206`). Issue sets no bound; the README feedback section already documents a 100-item cap, so this is a known-bound divergence, not a spec breach.

## Plan drift

- Plan status: not recorded — no plan file list to diff against. Files changed (`README.md`, `factory/dashboard.html`, `factory/dashboard.py`, `tests/test_factory.py`) match the issue's **Touches** list exactly; no extra files.
- Missing planned proof: the issue exit gate also requires "Running `factory dashboard` displays the queue without changing labels, reviews, branches, or merge state." The gate report records only `test: PASS`; no runtime/display evidence is recorded. The test (`review.diff:277-284`) is the only non-mutation evidence. Not a code defect; noting the gap.

## Required fixes

None.

## Optional suggestions

1. `review.diff:181-191` — prefer `readiness.get("state")` when a head-bound readiness row exists; recompute only when absent. Removes a second copy of dispatch's state machine that can drift (e.g. `skipping` handling, `confirmed` head).
2. `review.diff:86` — drop the unreachable `pr.reason ||` clause, or record a `reason` on `review-result` if the intent was to surface reviewer findings.
3. `review.diff:167` — wrap to ≤100 cols per `pyproject.toml:37`.
4. `CHANGELOG.md` has an `Unreleased` section with per-issue entries (`snapshot/CHANGELOG.md:3-27`); no documented rule requires one, but an entry for #9 would match the existing pattern.

VERDICT: APPROVE

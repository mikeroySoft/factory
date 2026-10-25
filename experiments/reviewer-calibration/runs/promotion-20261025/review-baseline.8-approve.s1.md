## Context

`./review.diff` and `./snapshot` are absent in this sandbox; reviewed `git diff 2f5e7ac..b1defa4` (merge-base of HEAD with `origin/main` → gated head `b1defa4 "Track fail-closed readiness for external PR reviews"`) against the checked-out tree at `b1defa4`. Issue #8 fetched from GitHub (1 comment: triage + agent brief, no scope changes). No `AGENTS.md`/`CONTRIBUTING.md` in the repo; standards sources are `README.md`, `CHANGELOG.md`, `docs/`, `pyproject.toml`.

Files changed: `factory/dispatch.py` (+49/−3), `tests/test_factory.py` (+65).

## Standards

- `factory/dispatch.py:274-312` — `review_readiness` reuses `run(..., check=False)`, `record()`, `lifecycle.read_events`, same shape as `open_blockers` (`dispatch.py:155-162`). No new state store, no new abstraction; consistent with `docs/autonomy-execution-plan.md:176` ("reuse `.factory/events.jsonl` through the existing journal writer").
- `factory/dispatch.py:276-282` — only `gh pr checks` / `gh pr view` are issued (read-only); `tests/test_factory.py:1426` asserts no `gh pr merge`. Matches README.md:349 "never merge external PRs" and the issue's out-of-scope list.
- `README.md:349` still states the review lane "does not … monitor CI". The diff now polls `gh pr checks` on every pass (`dispatch.py:232-233`, `276-279`), so that sentence is false after merge; `CHANGELOG.md:3-13` carries one `Unreleased` bullet per landed issue and has none for #8. No documented rule mandates README/CHANGELOG edits per PR and the issue's *Touches* names neither file → not blocking, listed under Optional.
- `pyproject.toml:37` `line-length = 100`; five added test lines exceed it (`tests/test_factory.py:1396,1397,1416,1421,1423`). The file already has 136 such lines and the gate doesn't lint → not a convention in practice; not reported.
- Smell baseline: no new abstraction, no duplication (the two `review_readiness` call sites at `dispatch.py:233` and `:271` are the two distinct moments the issue requires: periodic tracking and post-review). Nothing flagged.

## Spec (issue #8)

| Exit-gate criterion | Diff evidence | Status |
|---|---|---|
| Missing checks not ready | `dispatch.py:292` `bool(rows)` → `valid=False` → `ci_pending`; test `tests/test_factory.py:1393` `("[]", 0, "ci_pending")` | met |
| Unparsable checks not ready | `dispatch.py:284-291` swallow `ValueError/TypeError`, `rows=[]`; `:292-296` rejects non-dict rows/unknown buckets; tests `:1394,1395,1398,1399,1400` | met |
| Pending not ready | `:307-309` requires all `pass`; tests `:1396,1401` | met |
| Failed / cancelled not ready | `:305-306` → `ci_failed`; tests `:1397,1402,1403` | met |
| Approval + passing required checks, same head → ready | `:297-299` review matched on `head`; `:307-310` `confirmed and valid and returncode==0 and all pass`; test `:1406` → `"ready"` | met |
| New head SHA clears readiness | `:287-289` re-reads live head; `:297-299` review lookup keyed on that head → `review_pending`; tests `:1412-1419` (push mid-query and new list head) | met |
| Distinct states: CI pending, CI failed, changes requested, ready | `:300-310` emits `review_pending`/`changes_requested`/`ci_pending`/`ci_failed`/`ready` | met |
| No `gh pr merge` for external PR | `:1426` | met |
| Events via existing `record()` | `:311-312` `record("review-readiness", pr, head, state, review_head, checks)` | met |
| `python -m unittest` passes | gate `test: PASS` | met |

Behaviour change outside the brief: `dispatch.py:217-220` now keeps `APPROVE`d PRs in intake after GitHub consumes the review request. Required by the issue's "track … readiness" — without it an approved PR would never get a `ci_*`/`ready` event — so in scope, not creep.

Fail-closed review: `:287-289` keeps `confirmed=False` when `gh pr view` fails or returns an empty `headRefOid`; a parse error in `checks.stdout` short-circuits before the head refresh, so `confirmed` stays `False` and `ready` is impossible. `:305` set-membership on `row["bucket"]` is only reached when `valid` guarantees string buckets, so the `[]`-bucket case at `:1394` can't raise. No correctness or security defect found.

## Plan drift

Plan status: not recorded → no plan to diff against. Both changed files (`factory/dispatch.py`, `tests/test_factory.py`) are the files named in the issue's *Touches*; the third item (`.factory/events.jsonl` schema) is exercised through `record()` at `dispatch.py:311`. The issue's only named proof (`python -m unittest`) is covered by the gate's `test: PASS`. No drift exposing a defect.

## Required fixes

None.

## Optional suggestions

1. `README.md:349` — drop "monitor CI" from the "does not" list and document the `review-readiness` event (`pr`, `head`, `state`, `review_head`, `checks`) next to `review-intake` at `README.md:337-338`; add an `Unreleased` bullet `(#8)` in `CHANGELOG.md:3`. Ponytail: three lines of docs vs. a README that now contradicts the code.
2. `factory/dispatch.py:232-233` — `review_readiness` records an event and issues two `gh` calls for every tracked open PR on every dispatch pass, including PRs already `ready`, escalated, or `changes_requested`. Journal grows unbounded while any admitted PR stays open, and `runtime_events.BYTE_LIMIT`/`EVENT_LIMIT` (`factory/runtime_events.py:258-260`) tail windows shrink accordingly. Cheapest fix: skip `record()` when the last `review-readiness` for `(pr, head)` already has the same `state`. Not an acceptance criterion; add when journal growth is observed.

VERDICT: APPROVE

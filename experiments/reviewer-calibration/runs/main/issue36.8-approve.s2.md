## Review: issue #8 — CI/merge readiness for review lane

### Spec compliance (issue #8 exit gate)

| Criterion | Evidence | Status |
|---|---|---|
| Missing/unparsable checks not ready | `factory/dispatch.py:292-296` — `valid` requires non-empty list of `{name:str, bucket∈enum}`; `:306` ready requires `valid`. Tests `tests/test_factory.py:1391-1398` (`[]`, `null`, `not json`, `[{}]`, non-string bucket, unknown bucket). | Met |
| Pending not ready | `:304-308` — pending rows fail `all(bucket=="pass")`; exit 8 fails `returncode==0`. Tests `:1394,1399`. | Met |
| Failed/cancelled not ready | `:304-305` → `ci_failed`. Tests `:1395,1400-1401`. | Met |
| Approval + passing required checks, same head → ready | `:297-299` review matched on `head`; `:306-308` requires `confirmed and valid and returncode==0 and all pass`. Test `:1404`. | Met |
| New head SHA clears readiness | `:285-287` fresh `headRefOid` mismatch sets `confirmed=False` and rebinds `head`, so `:297-299` finds no matching review → `review_pending`. Tests `:1411-1418` cover both race (list stale, view fresh) and settled cases. | Met |
| No `gh pr merge` for external PR | No merge command in `review_readiness`; `:1428` asserts none across all passes. | Met |
| Distinct states ci_pending/ci_failed/changes_requested/ready | `:300-308`. | Met |
| Events via existing `record()` | `:309-310`. | Met |
| Dry run writes nothing | `:232-233`, `:271`; test `:1425-1427`. | Met |

Fail-closed invariants hold: every parse/exit failure path leaves `confirmed=False` or `valid=False`, and `ready` requires both.

### Standards

No AGENTS.md/CONTRIBUTING.md at this head. README conventions checked: events go through `record()` to `.factory/events.jsonl` (yes); lane never changes contributor branches or merges (yes); `gh` is the GitHub transport (yes).

### Required fixes

None.

### Optional suggestions

1. **Parse order couples head rebinding to checks parse** — `factory/dispatch.py:283-290`. `json.loads(checks.stdout)` runs before `json.loads(fresh.stdout)` in one `try`; if checks output is unparsable but the head moved, the event is recorded under the *stale* head with `ci_pending` instead of under the new head with `review_pending`. Still not ready (fail-closed), so not blocking. Parsing `fresh` in its own `try` would make the recorded `head` accurate independent of `gh pr checks` output.
2. **Two `review-readiness` rows per newly admitted PR per pass** — `:232-233` records before admission, `:271` records again after review. Also every tracked open PR (including approved ones, now retained via `:220`) gets a row every pass regardless of change. Journal growth only; no correctness impact.
3. **README now stale** — `factory/dispatch.py:276-310` makes the lane monitor CI, but README states "This lane does not change contributor branches, monitor CI, or merge external PRs," and the audit-trail event list omits `review-readiness`. Issue doesn't name docs in Touches, so not required; worth a follow-up edit.
4. **`skipping` required checks never reach ready** — `:306-308` demands every bucket `== "pass"`. Conservative and consistent with "fail closed"; note it if a repo has conditionally skipped required checks.

### Abstractions

One new function (`review_readiness`) — directly required by the "check-state query and readiness derivation" Touches entry; no extra abstraction introduced.

VERDICT: APPROVE

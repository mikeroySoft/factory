# Review: PR head b1defa4 vs issue #8

## Spec coverage (issue #8 exit gate)

| Criterion | Evidence | Status |
|---|---|---|
| Missing checks not ready | `factory/dispatch.py:293` (`bool(rows)` required for `valid`); test `tests/test_factory.py:1391` (`"[]"` → `ci_pending`) | met |
| Unparsable not ready | `dispatch.py:286-288` swallows `ValueError`, `rows` stays `[]`; tests `:1392,1393,1396,1397,1398` | met |
| Pending not ready | `dispatch.py:305` requires all `pass` and `returncode == 0`; tests `:1394,1399` | met |
| Failed / cancelled → `ci_failed` | `dispatch.py:303`; tests `:1395,1400,1401` | met |
| Approval + passing required checks, same head → `ready` | `dispatch.py:305-308` requires `confirmed and valid and checks.returncode == 0`; test `:1404` | met |
| New head SHA clears readiness | `dispatch.py:284-285` rebinds `head` to fresh `headRefOid`, review lookup `:298-300` keys on that head → `review_pending`; tests `:1411-1417` | met |
| Distinct states pending/failed/changes_requested/ready | `dispatch.py:301-308` | met |
| Events via existing `record()` | `dispatch.py:309-310` | met |
| No `gh pr merge` for external PR | no merge call in diff; test `:1426` asserts over all commands | met |
| Dry run writes nothing | `dispatch.py:232-233`; test `:1423-1425` | met |

Fail-closed invariant holds: `ready` is unreachable when the head re-read fails (`confirmed` stays `False`, `dispatch.py:280,283`), when `gh pr checks` exits nonzero, or when any bucket is `skipping`.

## Required fixes

None.

## Optional suggestions

1. `factory/dispatch.py:285-288` — `json.loads(checks.stdout)` runs before `json.loads(fresh.stdout)`. A malformed checks payload skips head confirmation, so the recorded `head` remains the stale intake SHA even if the PR moved (`:309`). Cannot yield `ready` (`confirmed` is `False`), but the event misreports the head. Parsing `fresh` first is a two-line reorder.
2. `factory/dispatch.py:232-233` and `:271` — a newly admitted revision records two `review-readiness` rows per pass: a `review_pending` row before `review_external_pr` runs, then the real state after. The first row is noise in `events.jsonl`; skipping the pre-review call when `(n, head)` has no `review-intake` yet would remove it.
3. README "This lane does not change contributor branches, monitor CI, or merge external PRs" is now inaccurate for the CI-monitoring clause, and the audit-trail event list does not mention `review-readiness`. Issue Touches excludes README, so not blocking; worth a follow-up doc edit.
4. `factory/dispatch.py:220` — admitting PRs with a prior `APPROVE` keeps every approved PR in the lane (two `gh` calls per PR per pass) until it closes. Intentional for continuous readiness tracking; note the cost.

No net-new abstractions introduced; `review_readiness` is a single function matching the brief's "readiness derivation" in `dispatch.py`.

VERDICT: APPROVE

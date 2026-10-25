**Context.** `./review.diff` and `./snapshot` are absent from the sandbox; the checkout is detached at the gated head `b1defa4` ("Track fail-closed readiness for external PR reviews"), so I reviewed `git diff HEAD~1..HEAD` (2 files: `factory/dispatch.py`, `tests/test_factory.py`). Issue #8 + triage comment fetched from GitHub (gh unauthenticated; web fetch worked). Repo has no `AGENTS.md`/`CONTRIBUTING.md`; standards sources are `README.md` and the reviewer contract in `CHANGELOG.md`.

## Standards
- No documented convention breached. `record()` reused for the new `review-readiness` event, `run(check=False)` reused for `gh` calls, existing `ticket_lock` scope respected, dry-run guarded at `dispatch.py:232` and `:268`.
- Doc staleness: `README.md:349` still states the lane "does not … monitor CI"; `dispatch.py:276-279` now queries `gh pr checks`. Sibling commits for #5–#7 also did not touch README/CHANGELOG, so no documented rule mandates it → nit.

## Spec (issue #8 exit gate)
| Criterion | Evidence | Status |
|---|---|---|
| missing / unparsable / pending / failed / cancelled ⇒ not ready | `dispatch.py:292-296` (`valid` requires non-empty list of known buckets), `:305-310`; tests `test_factory.py:1391-1404` | met |
| approval + all required checks pass, same head ⇒ ready | `dispatch.py:307-310` requires `confirmed and valid and returncode==0 and all pass`; test `:1404` | met |
| later head clears readiness | fresh `headRefOid` re-fetched `:280-289`, review matched on current head `:297-299`; tests `:1410-1417` | met |
| no `gh pr merge` for external PR | no merge call added; asserted `test_factory.py:1426` | met |
| distinct states CI pending / CI failed / changes requested / ready | `:300-310` (`review_pending`, `changes_requested`, `ci_pending`, `ci_failed`, `ready`) | met |
| events via existing `record()` | `:311-312` | met |
| out of scope (no branch pushes, no dashboard) | untouched | met |

Fail-closed properties hold: any parse failure leaves `confirmed=False`, so `ready` is unreachable.

## Plan drift
Plan status "not recorded" → no planned file list or proof to diff against. Both changed files are named in the issue's *Touches*. Gate `test: PASS` covers the issue's `python -m unittest` verification. No drift finding.

## Nits (non-blocking)
1. `dispatch.py:285-291` — `json.loads(checks.stdout)` precedes the `fresh` parse inside one `try`; when checks output is unparsable/empty (e.g. `gh pr checks --required` erroring with no required checks), head confirmation is skipped and the event is recorded against the stale list `head` even if the PR moved. Never produces `ready` (`confirmed` stays False), so fail-closed is intact; splitting the two parses would make the recorded head accurate.
2. `dispatch.py:233` + `:271` — readiness is recorded on every pass for every tracked PR (twice per intake pass), with two `gh` calls each, regardless of whether `(head, state)` changed. `events.jsonl` grows per tick; dedupe against the last `review-readiness` row for the PR if log growth matters.
3. `dispatch.py:276` — `README.md:349` ("does not … monitor CI") is now inaccurate.

```json
{"verdict":"APPROVE","findings":[{"severity":"nit","pass":"bug","path":"factory/dispatch.py","line":285,"summary":"Head confirmation is skipped whenever checks stdout is unparsable (single try block parses checks before fresh head), so the review-readiness event can record the stale list head after a push; never yields ready, so fail-closed holds."},{"severity":"nit","pass":"bug","path":"factory/dispatch.py","line":233,"summary":"review-readiness recorded every pass per tracked PR (and twice per intake pass via line 271) with two gh calls each, even when head/state unchanged; events.jsonl grows unboundedly per tick."},{"severity":"nit","pass":"spec","path":"factory/dispatch.py","line":276,"summary":"Lane now queries gh pr checks, but README.md:349 still states the review lane does not monitor CI; doc is stale."}]}
```
VERDICT: APPROVE

## Standards

Standards sources: no `AGENTS.md`/`CONTRIBUTING.md` in repo; `README.md` (review lane §, lines 335–349) and `docs/c1-read-interface-handoff.md:104` ("Keep the normal Python package dependency-free") are the documented conventions. Diff is stdlib-only, uses the existing `run`/`record`/`lifecycle.read_events` helpers, and respects the stated authority boundary (no merge path touched; `review-readiness` carries no `ticket` field, so `_head_evidence_matches` / the `approved` filter at `factory/dispatch.py:1255-1262` cannot match it).

**Required fixes:** none.

**Optional suggestions (non-blocking):**

- `README.md:349` still states "This lane does not change contributor branches, **monitor CI**, or merge external PRs." After `factory/dispatch.py:274-312` the lane does query `gh pr checks` and records `review-readiness`. Issue "Touches" does not list the README, so not blocking, but the sentence is now false; one-line edit.
- `factory/dispatch.py:284-291`: `rows = json.loads(checks.stdout)` precedes `current = json.loads(fresh.stdout)` in one `try`. If `gh pr checks` emits non-JSON (e.g. `--required` with no required checks → stderr message, exit 1, empty stdout), the head confirmation is skipped: `head` stays the listed SHA, `review` at `:297-299` can match a stale APPROVE, and the event reports `ci_pending` with the old `head` even if the PR moved. Never reaches `ready` (`confirmed` stays False), so fail-closed holds; splitting into two `try` blocks would make the advisory head/state accurate. Judgement call.
- `factory/dispatch.py:232-233`: `review_readiness` records an event for every lane PR on every pass regardless of change, and each call issues two `gh` subprocesses. `events.jsonl` grows by N rows per 10-minute pass per open lane PR, and the loop re-reads the full file per PR (`:221`, `:231`, pre-existing). Skipping `record` when `(head, state)` equals the last `review-readiness` row for that PR would bound growth. Not a documented rule.
- `factory/dispatch.py:289` reassigns the `head` parameter (possible Mysterious Name: the same identifier means "listed head" before and "fresh head" after). Judgement call; a second name would make `:297-312` easier to read.

## Spec

Issue #8 body + triage/agent brief comment (`issues/8#issuecomment-5622890098`).

| Criterion | Evidence | Status |
|---|---|---|
| Query check state for lane PRs, derive fail-closed readiness | `factory/dispatch.py:276-279` (`gh pr checks --required --json name,bucket`), `:292-310` default `review_pending`, `ready` only under `confirmed and valid and returncode==0 and all pass` | Met |
| Distinct states: CI pending, CI failed, changes requested, ready | `:300-310` → `review_pending`, `changes_requested`, `ci_pending`, `ci_failed`, `ready` | Met (extra `review_pending` is a natural pre-review state, not scope creep) |
| Ready requires approval of current head AND all required checks pass | `:297-299` review bound to `head`; `:307-310` | Met |
| Later head invalidates prior readiness | `:287-289` fresh `headRefOid` compared to listed head; mismatch → `confirmed=False`, lookup against new head → `review_pending`; test `tests/test_factory.py:1413-1420` | Met |
| Events via existing `record()` | `:311-312` | Met |
| Never merge external PRs | no `gh pr merge` added; `tests/test_factory.py:1381-1382` fails any unexpected command, `:1426` asserts no merge | Met |
| Tests: missing / unparsable / pending / failed / cancelled not ready; approval+pass same head ready; new head clears; no merge | `tests/test_factory.py:1391-1405` (`[]`, `not json`, `null`, `[{}]`, bad bucket type/value, pending, fail, cancel, skipping, pass-with-exit-1, pass → ready), `:1413-1420`, `:1426` | Met |
| Dry run writes nothing | `:232-233`; test `:1423-1425` | Met |
| Out of scope respected (no CI fixing, no pushes, no merges, no dashboard) | diff touches only `dispatch.py`/tests | Met |

Scope note: `factory/dispatch.py:217-220` widens lane admission to PRs with a prior lane APPROVE (previously only REQUEST_CHANGES). Required for the criterion to be observable — GitHub consumes the review request on approval, so without it an approved PR leaves the lane before CI can be tracked. Within the brief's intent; not creep.

No net-new abstractions beyond the single `review_readiness` function the brief implies.

**Required fixes:** none.

Summary — Standards: 0 required, 4 optional (worst: stale README:349 claim). Spec: 0 required, 0 optional.

VERDICT: APPROVE

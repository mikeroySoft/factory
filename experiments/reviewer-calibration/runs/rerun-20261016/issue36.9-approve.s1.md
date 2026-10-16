# Review: issue #9 — external PR review queue

## Standards
No AGENTS.md/CONTRIBUTING.md at this head; README is the only documented convention source. The diff's README section (`README.md:353-365`) matches the implemented behavior: read-only, states, head-bound review evidence, inbox links without mutation controls.

## Spec coverage

| Criterion | Evidence |
|---|---|
| Six states | `factory/dashboard.py:842-851`; test asserts all six at `tests/test_factory.py:2827-2829` |
| Number/title/author/head/last-reviewed SHA/verdict/CI state | `factory/dashboard.py:852-858` |
| GitHub link per row | `factory/dashboard.py:853`, `factory/dashboard.html:1136`; test `tests/test_factory.py:2831` |
| SHA-specific review data | `current` bound to `headRefOid` at `factory/dashboard.py:833-834`; old approval on stale head → `review_pending`, `ci_state: unknown` (`tests/test_factory.py:2835-2837`) |
| Stalled items in inbox, no mutation controls | `factory/dashboard.html:1160-1166`; briefing renders `reviewRow` plus a GitHub-pointer paragraph only (`factory/dashboard.html:1550-1551`) |
| No GitHub/journal mutation | `review_queue` is a pure projection; test asserts `events.jsonl` unchanged across snapshot (`tests/test_factory.py:2816-2822`) |
| README | `README.md:349-365` |
| Out of scope respected | No submit/merge/branch code added; no new service |

## Required fixes
None.

## Optional suggestions

1. **Escalation is not head-bound** — `factory/dashboard.py:837` takes the last `escalate` row for the PR regardless of `head`, while review (`:833`) and readiness (`:835`) are. After an escalation, a contributor pushing a new head stays `escalated` forever in this view (`:850-851` overrides every other state). The issue doesn't define escalation lifetime, so not blocking, but consider gating on `e.get("head") == head` or documenting the sticky semantics in README (`README.md:359-361` currently only states the old-approval rule).

2. **Opt-in proxy for consumed review requests** — `factory/dashboard.py:826-829` keeps a PR whose request was consumed only if a `review-result` with `APPROVE`/`REQUEST_CHANGES` exists. README says intake writes `review-intake` events (`README.md:333-335`); using those as the admission record would be the direct evidence and would also retain a PR recorded as `unknown`/`prompt_too_large` whose label was later removed. Edge case; current behavior is defensible.

3. **`INBOX_KINDS` not extended** — `factory/dashboard.html:1149` adds `external-review` to `WAITING` but `INBOX_KINDS` (`:1150`) has no entry. If any non-diffed code renders `INBOX_KINDS[item.kind]`, it would show `undefined` for these items. `[INFERENCE]` — usage not visible in the diff; worth a visual check in `factory dashboard` with one queued item.

4. **Ticket `escalate` rows with a `pr` key** — `factory/dashboard.py:814-816` buckets any row with `pr` set. If legacy ticket escalations carry a `pr` field, an agent PR labeled `needs-review` would inherit that escalation. `[INFERENCE]`; depends on `dispatch.record` payloads not in this diff.

VERDICT: APPROVE

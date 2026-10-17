## Review — issue #9, `311ba91..45d96f6`

### Spec coverage

| Criterion | Evidence |
|---|---|
| Six states from number/title/author/head/reviewed SHA/verdict/CI | `factory/dashboard.py:843-851` classification; fields emitted at `:852-858` |
| SHA-specific review data | `review_head`/`verdict` from last review-result (`:822`), state from head-bound `current` (`:834-835`) and `readiness` (`:836-837`); old approval on new head stays `review_pending`, tested `tests/test_factory.py:2832-2834` |
| Rows linked to GitHub | `factory/dashboard.html:1136` (`href: pr.url`, `rel: noopener`) |
| Stalled items in Inbox, no mutation controls | `factory/dashboard.html:1160-1166`; briefing is `reviewRow` + text only, `:1550-1551` |
| Snapshot test, all six states, links | `tests/test_factory.py:2821-2824`, `:2825-2829`; excluded closed/draft/other-reviewer (`:2795-2798`) implied by exact dict equality |
| No GitHub/journal mutation | snapshot only reads; journal unchanged asserted `tests/test_factory.py:2819` |
| README operating description | `README.md:353-365` |

Out-of-scope boundaries respected: no review submission, branch changes, merge, or new service.

### Required fixes

None.

### Optional suggestions

1. `factory/dashboard.py:838,850-851` — `escalate` lookup is not head-bound, unlike `current`/`readiness`. If dispatch ever re-admits a new head after an escalation `[INFERENCE: depends on #8 behavior not in this packet]`, a fresh APPROVE+green head would still render `escalated` and keep an Inbox item. Binding `escalation` to `head` (the test already records `head="head-6"`, `tests/test_factory.py:2814`) would match the README claim at `README.md:360-361` that stale evidence never promotes/demotes a new head. Not blocking: if escalation is terminal for the lane, current behavior is correct.
2. `factory/dashboard.py:828-831` — opt-in via consumed review request requires a `review-result` with `APPROVE`/`REQUEST_CHANGES`. A PR admitted by review request whose result was `unknown` (e.g. `prompt_too_large`, README.md:346-348) and whose request GitHub has since consumed disappears from the queue, although `README.md:354` says consumed admissions are listed. Keying on `review-intake` rows would close that gap `[INFERENCE: event name per README.md:331]`.
3. `factory/dashboard.html:1148-1149` — `external-review` is added to `WAITING` but not `INBOX_KINDS`; if `INBOX_KINDS[i.kind]` is used as a heading elsewhere in the file (not in diff), these items would label as `undefined`. Verify on the running dashboard.

No net-new abstractions beyond the brief; `review_queue()` is a single-use projection function matching the existing `build_ticket` pattern.

VERDICT: APPROVE

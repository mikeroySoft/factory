## Spec (issue #9)

- Six states classified in `factory/dashboard.py:843-851`; all six exercised and asserted in `tests/test_factory.py:2823-2826`, with per-row URL/head/author/title checks (`tests/test_factory.py:2827-2831`) and SHA-specific review data (`tests/test_factory.py:2832-2838`: old-head approval on PR 1 stays `review_pending`, `ci_state` unknown; PR 5 bound to `head-5`). Exclusion of other-reviewer request, closed, and draft PRs (`tests/test_factory.py:2794-2797`) is verified implicitly by the exact state-dict equality.
- Row fields (number, title, author, head, last reviewed SHA, verdict, CI state, state) emitted at `factory/dashboard.py:852-857` and rendered at `factory/dashboard.html:1135-1143`; each row links to GitHub (`factory/dashboard.html:1136`).
- Inbox surfaces changes-requested / CI-failed / escalated / stalled pending reviews (`factory/dashboard.html:1160-1166`) with a GitHub link and explicit no-mutation text (`factory/dashboard.html:1550-1551`); no new `/api/act` paths added.
- No mutation on snapshot: `review_queue` is a pure projection; journal unchanged asserted at `tests/test_factory.py:2821`.
- README documents the lane (`README.md:349-365`), matching the implemented states, CI-evidence source, and inbox behaviour.

## Standards

No AGENTS.md/CONTRIBUTING.md at this head. README "Review" convention (`path:line` citations, read-only lane, no merge/branch changes) is respected; header copy "read-only · required CI last observed by dispatch" (`factory/dashboard.html:331`) matches README `README.md:353-354`.

## Non-blocking observation

- `factory/dashboard.py:838,850`: `escalation` is the last `escalate` row for the PR regardless of `head`, so a PR escalated on an old head remains `escalated` after a new push until a human clears it. README does not define escalation as head-bound, and the issue text does not either, so this is a design choice, not an acceptance failure. Worth confirming it is intended.

VERDICT: APPROVE

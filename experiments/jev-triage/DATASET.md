# Jev triage dataset

## Snapshot contract

`cases.json` freezes 40 real public issues from [`mikeroySoft/factory`](https://github.com/mikeroySoft/factory) as retrieved through GitHub's read-only GraphQL API at `2026-09-16T21:29:11Z`. The replay input is only `issue.number`, `issue.title`, `issue.body`, and an intentionally empty `issue.comments` array.

This is a **creation-title/body-only replay**, not a reconstruction of the full historical triage state. The runner must not send `provenance` or this document to either model. In particular, labels, current state, assignees, projects, milestones, reactions, timeline events, triage comments, later edits, and later outcomes are not model input.

## Temporal evidence

- GitHub reported 57 public issues, created from 2026-09-04 through 2026-09-12.
- Thirty-nine selected bodies have `lastEditedAt = null` and `includesCreatedEdit = false`; their current public body is therefore creation-equivalent according to the API.
- Issue #5 had one public body edit. Its `userContentEdits` connection returned exactly two byte-complete versions: one equal to the current `Issue.body` and one predecessor while `includesCreatedEdit = true`. The predecessor is frozen here as the creation body. No selected case uses a contemporary-body fallback.
- A repository-wide `RenamedTitleEvent` query returned one rename, for excluded issue #34. No selected issue had a public title-rename event, so selected titles are marked `creation_equivalent_no_public_rename_event_observed`. This is API evidence, not a claim that deleted or unavailable events cannot exist.
- Comments are omitted for every case. Inspection showed that comments immediately preceding readiness labels were commonly the triage answer itself (`Triage:`) or a manager verdict; the other apparent pre-triage comments included approved narrowing or outcome records. A blanket empty-comments rule avoids inventing a cutoff and prevents those outcomes from leaking.

Each case records the public issue URL, API retrieval time, issue creation time, title/body temporal status, comment policy, and SHA-256 of the exact frozen body.

## Deterministic selection

1. Query `repository.issues(first: 100, orderBy: {field: CREATED_AT, direction: ASC})` and request `Issue.body`, `lastEditedAt`, `includesCreatedEdit`, `userContentEdits`, comments, label events, and title-rename events.
2. Keep every issue with no public body edit and no direct body/title statement of its own live triage routing outcome.
3. Exclude outcome-bearing creation bodies: #13, #56, #58, #59, #67, and #88-#91. These directly name or prescribe their own `factory-held`, `needs-triage`, `ready-for-agent`, or `ready-for-human` routing/hold state.
4. That leaves 39 never-edited cases. Fill the single remaining slot with the lowest-numbered edited issue whose creation version is exactly recoverable and has no public title rename or direct routing outcome leak: #5.
5. Sort the 40 case numbers ascending. Assign one-based positions 1, 5, 9, ..., 37 to `development`; assign the other positions to `evaluation`.

Development cases (10): `#1, #5, #10, #15, #19, #36, #53, #72, #87, #99`.

Evaluation cases (30): `#2, #3, #4, #7, #8, #9, #11, #12, #14, #16, #17, #18, #20, #21, #35, #37, #41, #49, #54, #62, #70, #79, #85, #86, #96, #97, #98, #100, #101, #102`.

## Exclusions

Seventeen of the 57 public issues are not cases:

- **Edited body, not needed after deterministic fill (8):** #6, #26, #27, #28, #34, #52, #55, #57. Public edit versions exist, but using additional edited cases was unnecessary; #34 also has a title rename.
- **Direct outcome/routing text in the creation body (9):** #13, #56, #58, #59, #67, #88, #89, #90, #91. These were conservatively excluded rather than asking a model to ignore explicit hold/readiness instructions.

No issue was fabricated or substituted to force a class. The public inventory has no `wontfix`, `duplicate`, `invalid`, or `question` example and no `not planned` closure, so the dataset does not pretend to cover those outcomes.

## Coarse coverage

The selected cases span six creation dates: 19 from 2026-09-04, 5 from 2026-09-07, 3 each from 2026-09-08, 2026-09-10, and 2026-09-11, and 7 from 2026-09-12. Frozen bodies range from 514 to 21,276 characters. Public titles/scopes naturally cover installation and configuration, dashboard and doctor behavior, PR review, manager/worker behavior, metrics and lifecycle evidence, reviewer calibration, merge bugs, collaboration/read-only surfaces, Graphify handling, Factory Manager work, and roadmap/experiment scheduling. Source labels were used only to audit available variety and are absent from model input.

## Limitations

- The source is one public repository, one issue author, and a compressed eight-day authoring window; it is not representative of general software triage.
- Public edit and rename history is the best available temporal evidence, not proof against deleted or unavailable history.
- Omitting all comments prevents outcome leakage but removes genuine clarification context too.
- There is no adjudicated human ground truth in this dataset. It supports a controlled replay comparison, not an accuracy claim against historical human decisions.
- Collection made no model calls, GitHub mutations, production changes, dispatches, or merges and used no private logs.

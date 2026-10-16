## Findings

### 1. Native repository ID is queried but never passed to the collector (spec: `repository.id`; README contract in this diff)

- `factory/dashboard.py:125` adds `id` to the repository selection in `GRAPHQL`.
- `factory/dashboard.py:812` declares `repo: dict = {}`; no hunk in the diff ever assigns `repo = data["repository"]`.
- `factory/dashboard.py:889` passes `repository={"id": repo.get("id"), ...}` → always `None`.

Effect: for open PRs `head()` backfills the ID from `HEAD_QUERY`, so the defect is masked. For closed/merged PRs (`collect_details=False`), `repository.id` is `null` and `observation_id` (`factory/feedback.py:437-439`) is hashed over `repository_id: None`, even though the full-snapshot query already returned the native ID. This contradicts the README text added in the same diff, `README.md:720-721`: "the schema-1 envelope still carries the caller's independently known repository/PR identities". Spec: "Use the GitHub native repository ID". The new `id` field at `dashboard.py:125` is dead. `tests/test_factory.py:2537-2541` asserts `pr.id` for historical PRs but not `repository.id`, so this is untested.

Fix: assign `repo = data["repository"]` after `data = github()` in `snapshot()`, and assert `repository.id == REPO["id"]` for the merged/closed cases in `test_historical_prs_add_no_feedback_reads_but_keep_schema_1`.

### 2. Exit gate 4 regression boundary for `--runtime-json` is absent

Issue exit gate 4 requires a focused check that "`factory dashboard --runtime-json` performs no feedback/GitHub read and its schema stays unchanged". `tests/test_factory.py:2397` (`FeedbackSnapshotTest`) and `tests/test_feedback.py` contain no runtime-json case; the only noncollection coverage is the `collect_details=False` producer test (`tests/test_feedback.py:276`) and the full-snapshot call-count test (`tests/test_factory.py:2518-2528`). The README claim at `README.md:705-706` ("`--runtime-json` does not invoke this collector") is therefore undemonstrated by the diff.

### Non-blocking observations

- `factory/dashboard.py:220-221`: the shared transport now raises on any GraphQL `errors` entry, where the previous code returned `["data"]` regardless. [INFERENCE] `gh api graphql` already exits nonzero on GraphQL errors, so this is likely unreachable in production; but if reachable, a single field error (e.g. upstream repo lookup) would now discard the entire legacy snapshot instead of its prior partial behavior. Worth confirming before relying on it.
- `factory/feedback.py:16` imports `lifecycle._lifecycle` (private). Issue asked to "reuse accepted bounded lifecycle/event readers"; no documented convention is violated, noted only because the private symbol has no stability contract.

Standards axis: no AGENTS.md/CONTRIBUTING.md at this head; README conventions (event contract, runtime-json network-free contract, briefing budget behavior) are respected by the diff except as noted in finding 1 (README text contradicted by implementation).

VERDICT: REVISE

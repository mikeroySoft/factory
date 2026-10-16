# Review: #79 B2 source-versioned PR feedback (head `e20ebcd`)

Gate (conflict-markers/test/leak-scan) PASS is taken as given; findings below are defects the gate would not detect.

## Required fixes

### R1. Repository native ID from the full snapshot is fetched and then discarded
- `factory/dashboard.py:124` adds `id` to the `repository{...}` GraphQL selection.
- `factory/dashboard.py:812` declares `repo: dict = {}`; `factory/dashboard.py:889` passes `repository={"id": repo.get("id"), ...}`. No line in the diff assigns `repo` [INFERENCE: `repo` is a name introduced by this diff, so no unchanged line can populate it].
- Effect: `repository.id` reaching `feedback.collect` is always `None`. For open PRs it is back-filled by `HEAD_QUERY` (`factory/feedback.py:146-152`), which also neuters the `identity_conflict` cross-check at `:146-149` (`None not in (None, …)` is always false). For `collect_details=False` (closed/merged, `dashboard.py:896`) `repository.id` is emitted null and `observation_id` (`feedback.py:437`) is hashed without the repository identity, even though the snapshot already holds it.
- Rule: issue schema — `repository.id`: "Use the GitHub native repository ID"; "Flattening consumers must retain the containing repository, PR, and owner identities." `tests/test_factory.py:2528-2537` asserts `pr.id` but not `repository.id`, which is why this passed.
- Fix: assign `repo = data["repository"]` (or equivalent) in the `try` at `dashboard.py:814-828`, and extend the historical-PR subtest to assert `observed["repository"]["id"] == REPO["id"]`.

### R2. Exit gate 4 regression boundary for `--runtime-json` is not in the diff
- Issue exit gate 4: "`factory dashboard --runtime-json` performs no feedback/GitHub read and its schema stays unchanged" is a named regression check to add. The diff adds `tests/test_factory.py:2397-2538` and `tests/test_feedback.py` but no test exercises the runtime-JSON path against `feedback.collect`/`github`.
- If a pre-existing F03 test already asserts no `gh`/subprocess on `--runtime-json`, cite it in the handoff and this is satisfied; otherwise add one assertion that `feedback.collect` and `dashboard.github` are not invoked on that path. [INFERENCE: existing coverage unknown from the packet.]

## Optional suggestions (non-blocking)

- **Closed/merged PRs skip detail reads** (`dashboard.py:886-887,896`; `feedback.py:21-23`): not in the issue text or any approved scope comment. It is a defensible bound and renders as explicit `unavailable`/`not_collected`, but it is a producer-policy decision #15 must know about; state it in the handoff as a deviation, not just in README.
- **`run_attempt` / `source_updated_at` for check runs are structurally null** (`feedback.py:300-301`): the REST check-runs object has `started_at`/`completed_at` and no `run_attempt` [INFERENCE from the API shape], so `row.get('run_attempt')` and `row.get('updated_at')` never populate. Consequences: every truncated check-run body triggers `change_detection_incomplete` (`feedback.py:201-202`), and `tests/test_feedback.py:117,119` exercise a field the real provider never emits. Consider `completed_at` as the update signal; rerun identity is already covered by new `node_id`.
- **`github()` now raises on any GraphQL `errors` array** (`dashboard.py:220-221`). If `gh api graphql` already exits nonzero on GraphQL errors this is dead code; if it does not, the existing full snapshot changes from partial data to total `github:` failure, which is a behavior change for existing consumers outside the brief. Verify which and either drop the check or scope it to the feedback queries.
- **`lifecycle._lifecycle` private import** (`feedback.py:392`): cross-module use of an underscore-prefixed validator; a public alias would keep the contract explicit for the #15 consumer.
- **File-level review comments** (`line: null`, not outdated) are mapped to `relevance: unknown` plus a `threads` partial gap (`feedback.py:421-423`); that flags a complete read as partial for a legitimate provider shape.
- **`dataset:` keys passed to `h()`** (`dashboard.html:994,1037,1059,1085`): only works if the existing `h` helper special-cases `dataset`; if it falls through to `setAttribute`, the data attributes used by the smoke/browser checks won't exist. Not verifiable from the diff.

No net-new abstractions beyond the brief were introduced; `feedback.py` is the single module the issue requested.

VERDICT: REVISE

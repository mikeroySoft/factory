## Findings

### 1. Repository native ID is fetched but never wired into the collector — `factory/dashboard.py:812`, `factory/dashboard.py:889`
`repo: dict = {}` is declared at :812 and nothing in the diff assigns it, yet :889 passes `repository={"id": repo.get("id"), ...}`. The diff adds `id` to the top-level `repository` selection in `GRAPHQL` (:124) specifically for this and then discards it. Consequences: for open PRs the ID only arrives via the extra `HEAD_QUERY` read; for closed/merged PRs (`collect_details=False`) `repository.id` is always `null` even though the full snapshot already observed it. Spec: "Use the GitHub native repository ID" and "Flattening consumers must retain the containing repository, PR, and owner identities." The test at `tests/test_factory.py:2473` mocks `{"repository": {"id": REPO["id"], ...}}` but never asserts `feedback.repository.id` for the merged/closed PRs (:2519-2525), so the gap is untested. [INFERENCE: intermediate snapshot lines are not in the hunk, but a new assignment to `repo` would have to appear as an added line and none does.]

### 2. Legacy full-snapshot transport now fails closed on any GraphQL `errors` array — `factory/dashboard.py:220-221`
Before this diff `github()` returned `json.loads(...)["data"]` unconditionally; GitHub routinely returns `data` plus a non-empty `errors` array for partial failures (e.g. an unreadable upstream repository, a single forbidden field). The new check raises `RuntimeError("GitHub query was incomplete")` for the default snapshot query too, which is caught at :829 and yields an empty `issues`/`prs` with an error string. That changes the existing `--json`/`/api/snapshot` contract from "partial data" to "no data" — contrary to "Preserve the existing `pr.checks`, `pr.comments`, ... current snapshot fields for their existing consumers" and exit-gate 4. If the intent is strictness for the feedback reads, gate it on `query is not None`.

### 3. Exit-gate 4 regression boundary for `--runtime-json` is absent — `tests/test_factory.py:2397-2525`
The new `FeedbackSnapshotTest` covers full-snapshot, provenance, revision and historical PRs, but no added test asserts that `factory dashboard --runtime-json` performs no `feedback.collect`/`github` call and keeps its schema. The issue lists this as a required focused regression boundary; README :705-706 claims it.

### Risk (non-blocking)
- `factory/dashboard.py:876-878`: `sh(["git", "rev-parse", "HEAD"], cwd=source_root)` runs for every full snapshot whenever any PR exists. If `sh` raises on nonzero exit (not visible in the diff), a non-git installed package (`uv tool install`, documented in README) would abort `snapshot()` rather than produce `revision=None`. The tests exercise only ignored/tracked/dirty inside a git repo (:2470-2492), never a non-repository install.
- `factory/feedback.py:300`: `ownership_unverified` is recorded as a `pr` coverage gap, so `coverage.pr.status` becomes `partial` on every successfully read PR without a Factory claim. This conflates read completeness with ownership relation; `owner.relation` already carries that fact. Consumers treating `partial` as "not fully read" will misreport.

VERDICT: REVISE

# Review: #79 B2 PR feedback (head e20ebcd5)

## Required fixes

### 1. Snapshot-level repository native ID is fetched then dropped (`repo` is dead)
- `factory/dashboard.py:125` adds `id` to the repository GRAPHQL selection.
- `factory/dashboard.py:812` declares `repo: dict = {}` and nothing in the diff ever assigns it; `data["repository"]["id"]` is never read.
- `factory/dashboard.py:890` passes `repository={"id": repo.get("id"), ...}` → always `None`.

Trigger/impact: for every PR with `collect_details=False` (closed/merged, `factory/dashboard.py:895`), `head()` never runs, so `feedback.repository.id` is `null` even though the full snapshot already holds the native ID. `observation_id` (`factory/feedback.py`, final `digest({'repository_id': repository['id'], ...})`) is then computed over a null repository identity. The spec requires `repository.id` to be the GitHub native repository ID and that flattening consumers retain the containing repository identity (issue §Normative schema 1, `repository`; "Flattening consumers must retain the containing repository, PR, and owner identities"). README diff (`README.md`, "the schema-1 envelope still carries the caller's independently known repository/PR identities") documents behaviour the code does not deliver. Tests don't catch it: `tests/test_factory.py` `test_historical_prs_add_no_feedback_reads_but_keep_schema_1` asserts `pr.id` but not `repository.id`.

Fix: `repo = data["repository"]` (or `repo_id = data["repository"].get("id")`) inside the `try` after `data = github()`, and assert `repository.id == REPO["id"]` for the merged/closed cases in that test.

### 2. Exit-gate 4 regression boundary for `--runtime-json` is absent
Issue exit gate 4 (acceptance criterion): "`factory dashboard --runtime-json` performs no feedback/GitHub read and its schema stays unchanged". The diff adds `FeedbackSnapshotTest` (`tests/test_factory.py:2397+`) with three tests; none exercises the runtime path, and no existing runtime test is updated. The runtime branch is untouched by the diff so the risk is low, but the criterion names the check explicitly. A single test that runs the runtime projection with `feedback.collect` and `dashboard.github` patched to raise satisfies it.

## Optional suggestions (non-blocking)

- **Producer revision outside a git checkout** (`factory/dashboard.py:876-880`): `sh(["git","rev-parse","HEAD"], cwd=<site-packages>/factory)` runs on every full snapshot. If `sh` raises on nonzero exit, a pip/uv-installed engine with no enclosing git repo would fail `snapshot()` entirely. `[INFERENCE]` — `sh` isn't in the diff; confirm it returns `""` on failure or wrap in `try`. Also `git status --porcelain -- .` only checks the `factory/` package dir, so "clean source checkout" (README) is narrower than stated.
- **Hand-rolled journal parse instead of the documented reader** (`factory/dashboard.py:853-874`): issue says "Reuse … accepted bounded lifecycle/event readers for provenance". README documents `factory.lifecycle.read_events` as the tolerant reader. The inline parser marks `provenance_complete=False` on any non-JSON line, including the NUL-invalidated tails the journal contract says readers skip; after one torn row within the 2 MB tail every open PR gets `reviews` partial/`truncated=true` and a changed `observation_id` until that row scrolls out. Conservative, not wrong, but worth reusing `read_events` and only flagging `cut`.
- **File-level review comments become `unknown`** (`factory/feedback.py`, relevance loop: `elif outdated is None or value['location']['line'] is None`): a non-outdated comment with no line (file-level) is forced `unknown` plus a `comment_position_unknown` gap, even though the provider supplied `isOutdated=false` and a matching commit. Consider only the `outdated is None` condition.
- **Gate report labelled "legacy"** (`factory/dashboard.html`, `taskFacts`: "Legacy local gate failures" / "Legacy local gate context"): README documents the gate as "the evidence of record". The feedback schema doesn't supersede the deterministic gate; "legacy" for `pr.verdicts`/review comments is accurate, for the gate it misdescribes documented authority. Text-only.
- **Per-PR 30 s budget × open PRs** (`factory/feedback.py:79`, `deadline` per `collect` call; `factory/dashboard.py:889`): a snapshot with N open Factory PRs may spend up to 30·N s plus ~6 `gh` invocations each. The 15 s HTTP cache doesn't bound a slow refresh. Acceptable under the spec's fixed constants; document the aggregate in README if operators run many concurrent tickets.
- `github()` now raises on any GraphQL `errors` for the main snapshot query (`factory/dashboard.py:219-220`). `gh api graphql` already exits nonzero on GraphQL errors `[INFERENCE]`, so this is likely a no-op, but if partial data was previously tolerated this is a behaviour change to the preserved snapshot path.
- `lifecycle._lifecycle` is a private symbol imported from `feedback.py` (`factory/feedback.py`, `valid = [e for e in events if lifecycle._lifecycle(e)]`); `lifecycle.py` is out of scope so a public alias can't be added here, but note the coupling.
- `test_factory_provenance_is_not_rendered_verdict_text` appends `{'body': 'VERDICT: APPROVE'}` as an *event* row, not a PR comment; the "ordinary PR comment" half of exit gate 1 is only covered implicitly via `test_full_snapshot_preserves_legacy_contract...` (`comments` with `VERDICT: APPROVE` → item kinds exclude it). Fine, but an explicit assertion would be clearer.

## Standards
No AGENTS.md/CONTRIBUTING.md at this head. README conventions (schema-1 documentation, bounded reads, read-only authority, unknown ≠ empty) are followed by the diff; README/CHANGELOG updated. Scope restriction to open PRs for detail reads is documented and reported as `not_collected`, distinct from unsupported; not a silent scope cut.

VERDICT: REVISE

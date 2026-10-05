# Review: #79 B2 — source-versioned PR feedback

## Required fixes

### R1. `repo` is never populated in `snapshot()`; native repository ID is dead on the caller side
- `factory/dashboard.py:812` declares `repo: dict = {}`; no later line in the hunk (or elsewhere in the diff) assigns `data["repository"]` to it.
- `factory/dashboard.py:889` passes `repository={"id": repo.get("id"), …}` → always `None`.
- `factory/dashboard.py:124` adds `id` to the repository selection in `GRAPHQL`, but that value is never read.

Impact:
- Every `collect_details=False` envelope (closed/merged PRs, `factory/dashboard.py:895`) ships `repository.id: null` even though the full snapshot already fetched the native ID. Issue schema: "`repository`: `{id: string|null, …}`. Use the GitHub native repository ID" and "Flattening consumers must retain the containing repository… identities."
- For open PRs the contradiction check at `factory/feedback.py` (`head()`: `repository['id'] not in (None, identity(repo['id']))`) is vacuous because the caller side is always `None`; the collector can never detect a repository-identity conflict between the snapshot read and the detail read.
- `tests/test_factory.py:test_historical_prs_add_no_feedback_reads_but_keep_schema_1` asserts `pr.id` but not `repository.id`, so the gate did not catch this.

Fix: `repo = data["repository"]` inside the `try` after `data = github()`, and assert `observed["repository"]["id"] == REPO["id"]` for the merged/closed subtests.

### R2. Producer-revision probe can abort the whole full snapshot on installed hosts `[INFERENCE on sh()]`
- `factory/dashboard.py:875-879`: `sh(["git", "rev-parse", "HEAD"], cwd=source_root)`, `sh(["git", "ls-files", …])`, `sh(["git", "status", …])` run unconditionally whenever `raw_prs` is non-empty, in `Path(__file__).parent` of the installed package.
- Trigger: the documented install path (`uv tool install …`, README "Install") places `factory/` in a tool venv that is not inside any git worktree. `git rev-parse HEAD` exits 128 there.
- Impact: if `sh` raises on nonzero exit (not visible in the diff; the helper is pre-existing), `snapshot()` raises before `build_ticket` and the entire `--json` / `/api/snapshot` output fails for any repository that has at least one PR. This is outside the `try/except` that guards the GitHub read.
- The test (`tests/test_factory.py`, modes `ignored`/`tracked`/`dirty`) only exercises paths inside a temporary git repository; the outside-any-repo case is untested.

Fix: wrap the three calls so a failed `git` probe yields `revision = None` (the issue explicitly permits null: "`revision` is a verifiable source/build revision when available, otherwise null"), and add that case to the subtests. If `sh` already returns `""` on failure, demote this to a no-op and add the outside-repo subtest anyway so the contract is pinned.

## Optional suggestions

- **Open-PR-only detail collection is an unapproved scope narrowing.** `factory/dashboard.py:895` (`collect_details=raw.get("state") == "OPEN"`), README/CHANGELOG text. The issue never limits collection to open PRs; neither triage comment approves it. The engineering rationale (100 PRs × up to 8 `gh` calls × 30 s) is sound, but record it in the issue/handoff for explicit acceptance rather than only in README. Not blocking: schema-1 envelope with `unavailable`/`not_collected` coverage is still compliant with "partial or unavailable never means none".
- **Exit gate 4 regression for `--runtime-json`** is not in this diff. If existing F03 tests already assert no `gh`/subprocess invocation on that path, cite them in the handoff; otherwise add one assertion (`subprocess.run` patched to raise) to `tests/test_factory.py`.
- `factory/dashboard.py:220-221`: raising on any GraphQL `errors` key changes the pre-existing full-snapshot transport (previously partial `data` with `errors` was still consumed). `gh api graphql` usually exits nonzero in that case so it is mostly moot, but note it in CHANGELOG or drop it to keep the legacy contract literally unchanged.
- `factory/feedback.py` check-run branch: `author=row.get('creator')` — REST check runs carry `app`, not `creator`, so `author.login` is always null for `check_run`. Explicit null is schema-valid; using `app.slug` would be more informative.
- `factory/feedback.py` `item()`: on `source_identity_conflict` the removed item's `counts[source]` is not decremented, so the item cap can be reached one early. Cosmetic given `ITEM_LIMIT=100`.
- `factory/dashboard.html` relabels unrelated legacy facts ("Legacy local gate failures", "factory-approved label present") in `taskFacts`/`tabReview`. Presentation only; not requested by the brief, but harmless.

## Standards
No AGENTS.md/CONTRIBUTING.md at this head. README conventions (read-only authority, no model call, no mutation, bounded reads, explicit unknown vs empty) are followed in `feedback.py`, `briefing.py`, and the drawer. Tests use `unittest`, consistent with the documented gate command.

## Spec
Schema-1 keys, canonicalization, bounds, head-race handling, relevance/disposition independence, provenance-backed `factory_review`, briefing priority/omission reporting, and safe-DOM rendering match the issue text. R1 is the one concrete schema defect; R2 is a correctness risk on the documented install path.

VERDICT: REVISE

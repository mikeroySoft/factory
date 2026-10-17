# Review: #79 B2 — PR feedback collector

## Findings

### 1. Native repository ID fetched by the full query is never passed to the collector (spec / README contradiction)
- `factory/dashboard.py:124` adds `id` to `repository{...}` in `GRAPHQL`.
- `factory/dashboard.py:812` declares `repo: dict = {}`; no line in the diff ever assigns it from `data` (the only additions in the `try` are `raw_prs[n] = pr`, `:827`).
- `factory/dashboard.py:889` passes `repository={"id": repo.get("id"), ...}` → always `None`.
- `factory/feedback.py:92` therefore starts with `repository.id = None`. For open PRs `head()` back-fills it from `HEAD_QUERY`; for `collect_details=False` (closed/merged) it stays `null` even though the snapshot already holds the native ID.

Consequences: issue §Normative schema 1 (“Use the GitHub native repository ID”) is unmet on the `not_collected` envelope; README.md:+718–719 (“the schema-1 envelope still carries the caller's independently known repository/PR identities”) is false for `repository.id`; the new GraphQL field is dead. `tests/test_factory.py:2537` asserts `pr.id` for historical PRs but never `repository.id`, so the gap is untested. Fix: `repo = data["repository"]` in the `try` block; assert `observed["repository"]["id"] == REPO["id"]` in the historical-PR test.

### 2. Exit gate 4 regression boundary for `--runtime-json` is absent
Issue exit gate 4 requires a check that `factory dashboard --runtime-json` performs no feedback/GitHub read and its schema is unchanged. `tests/test_factory.py:2397–2538` (`FeedbackSnapshotTest`) and `tests/test_feedback.py` contain no runtime-json assertion. The dashboard diff does not touch the runtime path, so this is likely satisfied in behavior `[INFERENCE]`, but the acceptance criterion names the test, and the README claim at README.md:+705–706 is unproven by the suite.

### 3. Detail collection skipped for non-open PRs — scope reduction not in the issue
- `factory/dashboard.py:894` `collect_details=raw.get("state") == "OPEN"`; `factory/feedback.py:~130–134` (`request()`) records `not_collected` instead of reading.
- `factory/dashboard.py:824–826` (unchanged) prefers the **merged** PR for a ticket. Result: once a ticket merges, its Review drawer/Inbox/briefing show every source `unavailable`.

The issue defines the collector's bounds as the fixed constants only and says a present PR gets `unavailable/partial` coverage “when a feedback source cannot be read” — not when the engine declines to read. Cadence concern is legitimate (see 4), but this is an unauthorized narrowing documented as a feature (README.md:+715–723, CHANGELOG.md:+5). Either get explicit approval or collect for all PRs.

### 4. Snapshot cadence risk (not blocking, no fixed bound)
`factory/dashboard.py:888` calls `feedback.collect(github, …)` sequentially per selected open PR; each collector does up to 6 `gh` subprocesses with its own 30 s `DETAIL_TIMEOUT` (`factory/feedback.py:22`). 100 open PRs ⇒ up to ~600 `gh` calls and 50 min worst case inside the 15 s-cached snapshot. Issue: “Preserve full-snapshot cadence.” No snapshot-level bound exists in the diff.

### 5. Unverified failure mode of producer-revision probe `[INFERENCE]`
`factory/dashboard.py:876–878` runs `git rev-parse HEAD`, `git ls-files`, `git status` with `cwd=Path(__file__).parent` on every full snapshot with PRs. For a `uv tool`/pipx install the package directory is not a git repository; `sh()`'s behavior on nonzero exit is not in the diff/packet. `tests/test_factory.py:2443–2470` only exercises package paths inside a git repo (`.venv` under the test repo). If `sh` raises, every installed-host snapshot fails. Needs a non-git-location test or explicit handling.

## Verified OK
- `source_revision` fields (`factory/feedback.py:27–29`) and `observation_id` inputs (`:434–436`) match the issue exactly; `observed_at`, URLs, relevance excluded.
- Bounds constants `:18–22`; two-page/100-item/20 KB/32-error/timeout handling and dedup of errors (`gap`, `:~110–118`).
- Head race → all coverage `partial`, relevance `unknown`, final head used (`:~385–392`); outdated thread → `historical` regardless of SHA equality (`:~423–428`).
- Factory review requires lifecycle `result` with `returncode==0`, `parsed`, `head==actual_head`, enclosing `enter`/`exit`; VERDICT prose ignored (`:~405–417`).
- Legacy `checks/comments/verdicts/review_decision/gate_text` preserved and asserted (`tests/test_factory.py:2473–2480`).
- Briefing appends feedback after existing candidates, reports omission/shortening (`factory/briefing.py:+289–380`); per-item payload retains repository/PR/owner identities (`:+345–352`).
- HTML renders body via `pre()`/`h()` text nodes, links via `safeSourceURL` (`factory/dashboard.html:+1010–1013, +1087`).

VERDICT: REVISE

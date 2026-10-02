## Findings

### Required

**1. `github()` now discards the entire snapshot on any GraphQL partial error** — `factory/dashboard.py` (diff hunk `@@ -192,29 +193,33 @@`, lines `value = json.loads(proc.stdout)` … `if value.get("errors"): raise RuntimeError("GitHub query was incomplete")`).
Pre-diff, `github()` returned `json.loads(proc.stdout)["data"]` unconditionally; GitHub GraphQL routinely returns `data` plus a non-empty `errors` array (nullable sub-field failures: inaccessible `UPSTREAM` repository, `INSUFFICIENT_SCOPES` on `timelineItems`, a single deleted-author node). The existing full snapshot kept the partial issue/PR data in those cases. After this change the same response raises inside `snapshot()`'s `try`, lands in `errors.append(f"github: {exc}")`, and `issues`/`prs` stay empty — every existing ticket/PR field disappears. This violates the issue's "Preserve the existing `pr.checks`, `pr.comments`, `pr.verdicts`, `review_decision`, gate text, and current snapshot fields for their existing consumers" and "Preserve full-snapshot cadence and existing ticket/PR fields." The strict check is appropriate for the feedback detail queries (where `request()` already maps failures to coverage), but it must not apply to the default `query is None` snapshot path. Fix: gate the `errors` raise on `query is not None`, or let `feedback.collect` validate `errors` itself.

### Non-blocking / confirm

**2. Detail collection is skipped for non-open PRs** — `factory/dashboard.py` hunk `@@ -839,10 +847,53 @@`, `collect_details=raw.get("state") == "OPEN"`; `factory/feedback.py` `NOT_COLLECTED` path. The issue never authorizes limiting the collector to open PRs; its contract for a present PR is "return the schema-1 feedback object with explicit unavailable/partial coverage" when a source *cannot* be read, not when the engine declines to read it. The implementation keeps coverage `unavailable` with an explicit `not_collected` code and documents it (README/CHANGELOG hunks), so it is honest rather than fabricated, but it is a silent scope reduction relative to the spec. Confirm with the issue owner; if accepted, nothing further needed.

**3. `repo` identity for the snapshot path is not visibly populated** — `factory/dashboard.py` hunk `@@ -803,6 +808,8 @@` adds `repo: dict = {}` and later passes `repository={"id": repo.get("id"), ...}`; no `+` line assigns `repo` from `data["repository"]`, and the GRAPHQL hunk adds `repository{ id ... }` and `headRefOid` whose consumers are not in the diff. `[INFERENCE]` If the elided context does not already assign `repo`, `repository.id` is always `None` for `collect_details=False` PRs (open PRs recover it from `HEAD_QUERY`), making the added query fields dead weight. Cannot be confirmed from the diff alone; `test_historical_prs_add_no_feedback_reads_but_keep_schema_1` does not assert `repository.id`.

### Checked and consistent with spec
- Schema-1 envelope keys, item fields/nulls, enums, `REVISION_FIELDS`, `observation_id` inputs excluding `observed_at`/URLs/relevance (`factory/feedback.py` `REVISION_FIELDS`, final `digest({...})`).
- Fixed bounds (`ITEM_LIMIT`, `PAGE_LIMIT`, `BODY_LIMIT`, `ERROR_LIMIT`, `DETAIL_TIMEOUT`), initial/final head reads, head-race → `unknown` with both SHAs in reason, `thread_outdated=true` → `historical`, missing source SHA never filled from head.
- Factory review requires lifecycle `result` with `returncode==0`, `parsed`, `head==actual_head`, matching `enter`/`exit`; provenance-free `VERDICT` is not promoted (`test_factory_provenance_is_not_rendered_verdict_text`).
- Sanitized fixed error messages; one-source failure preserves other sources.
- `sources_for`: feedback appended after existing candidates, omissions/shortening reported separately (`feedback_omitted`, `feedback_shortened`), unsupported/missing schema → explicit notice, uninterpreted.
- `dashboard.html`: body via existing `pre()`, links via `safeSourceURL`, no new action control; `--runtime-json` path untouched.

VERDICT: REVISE

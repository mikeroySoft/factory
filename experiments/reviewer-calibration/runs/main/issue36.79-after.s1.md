# Review: PR for #79 (head e20ebcd)

## Required fixes

### R1. `snapshot()` never populates `repo`; the native repository ID from the full-snapshot query is dropped
- `factory/dashboard.py:812` adds `repo: dict = {}`; `factory/dashboard.py:124` adds `repository{ id }` to `GRAPHQL`; `factory/dashboard.py:889` passes `repository={"id": repo.get("id"), ...}`. No line in the diff assigns `repo` from `data["repository"]`, so `repo.get("id")` is always `None`.
- Consequence 1 (closed/merged PRs, `collect_details=False`): `repository.id` is `null` in every envelope although the snapshot already holds it. Violates issue "`repository`: `{id: string|null, ...}`. Use the GitHub native repository ID" and makes `observation_id` (`factory/feedback.py:437`) hash a null repository identity when a real one was available.
- Consequence 2 (open PRs): `repository['id']` is only ever set inside `head()` (`factory/feedback.py:166`). If the *initial* head read fails/times out (`request()` returns `None`), `item()` rejects every review, thread comment, check run and status with `identity_missing` (`factory/feedback.py:199-201`). A single failed `pr` source then erases all independently successful sources, violating "One failed source must not discard usable independent sources" and "A failed or partial reviews read cannot erase a successful checks read, and the converse also holds." `tests/test_feedback.py:239` only covers *final*-head failure; the test fixtures always supply `REPO["id"]` to `collect`, so this path is untested.
- Fix: assign `repo = data["repository"]` after `data = github()` (or pass `data["repository"]["id"]` directly). Add the initial-head-failure case to `test_feedback.py` with the caller-supplied ID and assert reviews/threads/checks items survive.

## Optional suggestions (non-blocking)

- `factory/feedback.py:361-362`: `ownership_unverified` is recorded as a `pr` coverage gap, which flips `coverage.pr.status` to `partial` for every non-Factory PR even when the read was complete. Ownership is already explicit in `owner.relation`; marking a complete read partial blurs the "complete empty vs. partial" distinction the issue requires. Consider keeping it in `owner.evidence`/`errors` without degrading coverage.
- `factory/feedback.py:63-66` + `factory/dashboard.py:887-897`: detail collection is restricted to `state == "OPEN"`. This is not in the issue text or the agent brief; it is defensible under "Preserve full-snapshot cadence" (100 PRs × 5 reads), and README/CHANGELOG document it, but state that justification in the handoff. Note the full query now fetches `headRefOid` (`factory/dashboard.py:150`) yet `pr.head_sha` stays `null` for uncollected PRs; if you keep the policy, that field is unused.
- `factory/dashboard.py:219-220`: new `if value.get("errors"): raise RuntimeError(...)` applies to the *existing* full-snapshot query as well. If `gh api graphql` ever returns exit 0 with partial `data` + `errors` (e.g. upstream repo permission), the whole snapshot now fails where it previously degraded. Likely redundant with `gh`'s nonzero exit, but it is a behaviour change on the legacy path the issue says to preserve.
- `factory/feedback.py:248-252`: on `source_identity_conflict`, `counts[source]` is not decremented after `result['items'].remove(previous)`, so the 100-item cap can under-count by one per conflict. Harmless, but easy to fix.
- `factory/dashboard.py:873-878`: three `git` subprocesses per snapshot when any PR exists, solely for `producer.revision`. Behaviour of `sh()` on a non-git install location (uv tool site-packages) is not visible in the diff — `[INFERENCE]` if `sh` raises, the full snapshot fails. Worth a guard or a test that patches `sh` to fail.
- `factory/briefing.py:209`, `:272-273`, `factory/dashboard.html:1067-1076`, `:1260-1266`: relabeling legacy verdict/gate/CI text as "Legacy … not source-versioned authority" goes beyond "preserve existing fields"; it's presentation only and within the touched sections, so not blocking, but it widens the diff beyond the brief.

## Standards
No AGENTS.md/CONTRIBUTING.md at this head. README/CHANGELOG updated as required; test gate command unchanged; `--runtime-json` path untouched in the diff.

VERDICT: REVISE

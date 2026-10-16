# Review: #79 B2 — source-versioned PR feedback

## Required fixes

### 1. Snapshot never captures the repository native ID it now queries
- `factory/dashboard.py:124` adds `id` to the repository node of `GRAPHQL`.
- `factory/dashboard.py:812` declares `repo: dict = {}`; no line in the diff assigns it from `data` after `data = github()` (`dashboard.py:816`). Since `repo` is new, no unchanged context line can set it.
- `factory/dashboard.py:889` passes `repository={"id": repo.get("id"), ...}` → always `None`.

Effect: for open PRs the ID is only recovered because `head()` back-fills it from `HEAD_QUERY` (`factory/feedback.py:~160`), so the snapshot-vs-detail `identity_conflict` cross-check never has a snapshot value to compare. For closed/merged PRs (`collect_details=False`), `tickets[].pr.feedback.repository.id` is `null` and `observation_id` hashes `repository_id: None` even though the full snapshot already returned the native ID. Spec, schema 1 `repository`: "Use the GitHub native repository ID". `tests/test_factory.py:2522-2533` asserts `pr.id` for historical PRs but not `repository.id`, so the gate did not catch it. Fix: `repo = data["repository"]` immediately after `data = github()`.

### 2. Exit gate 4 regression boundary for `--runtime-json` is absent
Issue exit gate 4: "Add focused regression boundaries: … `factory dashboard --runtime-json` performs no feedback/GitHub read and its schema stays unchanged". The diff's tests (`tests/test_factory.py:2397-2535`, `tests/test_feedback.py`, `tests/test_briefing.py:17-183`) exercise only `dashboard.snapshot()` and `feedback.collect`. No test invokes the runtime path with `feedback.collect`/`github` patched to fail. The structural guarantee (collector only called inside `snapshot()`, `dashboard.py:888`) is plausible but the criterion asks for the test.

## Optional suggestions

- `factory/feedback.py:402-403`: `ownership_unverified` is recorded as a `pr` coverage gap, so a fully read PR source reports `partial` whenever the PR lacks a closing-issue link. Owner relation already carries this (`owner.relation`); conflating it with read coverage makes the briefing emit "partial coverage is unknown, not empty" (`factory/briefing.py:334-336`) and the drawer show `pr partial` for every unlinked PR. Consider keeping it in `owner.evidence` only.
- Open-PR-only detail collection (`dashboard.py:886-888`, `feedback.py:364-367`, README/CHANGELOG) is a scope decision not in the issue or its comments; the schema's `pr.state` enum anticipates `closed`/`merged` collection. Documented and explicit (`not_collected`), so not blocking, but call it out in the handoff for #15.
- `factory/dashboard.py:875-879`: three `git` subprocesses per snapshot in the package directory. `sh`'s behavior on nonzero exit (installed package outside any git checkout) is not visible in the diff; if it raises, `snapshot()` fails entirely for installed users. `[INFERENCE]` — verify `sh` is `check=False`.
- `factory/dashboard.py:218-220`: `if value.get("errors"): raise RuntimeError(...)` now applies to the main snapshot query. Previously partial `data` with GraphQL `errors` was consumed. `gh api graphql` typically exits nonzero on GraphQL errors already, so likely moot; confirm before relying on it.
- `factory/dashboard.html:1088`: legacy verdict chips drop the `PASS`/`FAIL` class. Intentional de-emphasis, but it changes existing visual encoding of `pr.verdicts`.
- `factory/feedback.py:400-401`: once `events.jsonl` exceeds the 2 MB tail cap, every open PR's `reviews` coverage is permanently `partial`/`truncated`. Correct per spec ("unseen reviews are unknown"), but operators should expect this steady state.

VERDICT: REVISE

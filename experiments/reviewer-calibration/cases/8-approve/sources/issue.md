# Issue #8: Track CI and merge readiness for reviewed pull requests

## Scope

Track GitHub check state for pull requests in the opt-in review lane and derive a fail-closed readiness state from both review and CI evidence. Expose distinct states for CI pending, CI failed, changes requested, and ready when the factory review approves the current head SHA and all reported required checks pass. A later head SHA invalidates prior readiness. External pull requests must never be merged by this lane.

Blocked by: #7

## Touches

- `factory/dispatch.py`: check-state query and readiness derivation
- `.factory/events.jsonl` event schema through existing `record()` calls
- `tests/test_factory.py`: pending, failed, missing, passing, and stale-approval cases

## Exit gate

Tests prove that missing, unparsable, pending, failed, or cancelled checks are not ready; approval and passing required checks for the same head SHA are ready; a new head SHA clears readiness; and no `gh pr merge` command is issued for an external PR. Running `python -m unittest` passes.

## Out of scope

Fixing failed checks, pushing to contributor branches, merging external PRs, and dashboard rendering.

## Comment by @mikeroySoft (2026-09-10T17:39:16Z)

Triage: The issue is fully specified: it has a clear problem statement (track CI/merge readiness for the opt-in review lane), explicit acceptance criteria in the Exit gate section (missing/unparsable/pending/failed/cancelled checks are not ready; approval + passing required checks for the same head SHA is ready; a new head SHA clears readiness; no gh pr merge for external PRs), exact files to touch (factory/dispatch.py, .factory/events.jsonl via record(), tests/test_factory.py), a defined out-of-scope, and an observable verification command (python -m unittest). The 'Blocked by: #7' note is a dependency flag, not a specification gap, so the work is ready to be actioned once that dependency resolves.

Agent brief: In factory/dispatch.py, query GitHub check state for PRs in the opt-in review lane and derive a fail-closed readiness state. Expose distinct states for CI pending, CI failed, changes requested, and ready. Ready requires: factory review approves the current head SHA AND all required checks pass. A new head SHA invalidates/clears prior readiness. External PRs must never be merged (no gh pr merge). Emit events through existing record() calls to .factory/events.jsonl. Add tests in tests/test_factory.py covering: missing/unparsable/pending/failed/cancelled checks are not ready; approval + passing required checks for the same head SHA is ready; a new head SHA clears readiness; and no gh pr merge command is issued for an external PR. Verification: python -m unittest

# Issue #9: Show the external pull-request review queue in the dashboard

## Scope

Add an external pull-request review queue to `factory dashboard`. Show each opted-in PR with repository-local PR number, title, author, current head SHA, last reviewed SHA, review verdict, CI state, and one of: pending review, changes requested, CI pending, CI failed, ready, or escalated. Link each row to GitHub and include actionable stalled items in the existing inbox without adding mutation controls.

Blocked by: #8

## Touches

- `factory/dashboard.py`: review-queue snapshot data
- `factory/dashboard.html`: queue and inbox rendering
- `tests/test_factory.py`: snapshot classification and response shape
- `README.md`: operating description of the review lane

## Exit gate

A dashboard snapshot test covers all six states and verifies PR links and SHA-specific review data; `python -m unittest` passes. Running `factory dashboard` displays the queue without changing labels, reviews, branches, or merge state.

## Out of scope

Submitting reviews from the dashboard, fixing contributor branches, merging external PRs, and adding a separate dashboard service.

## Comment by @mikeroySoft (2026-09-10T17:38:52Z)

Triage: The issue is fully specified: it has a clear problem statement (add an external PR review queue to the dashboard), explicit files to modify (factory/dashboard.py, factory/dashboard.html, tests/test_factory.py, README.md), clearly bounded out-of-scope items, and a concrete exit gate with observable done-conditions (snapshot test covering all six states, verified PR links and SHA-specific review data, `python -m unittest` passing, and no mutation of labels/reviews/branches/merge state). It does not require design judgment, nor does it touch release, signing, or security policy, and its blast radius is contained to this repository. The 'Blocked by #8' note is a dependency/sequencing concern rather than missing information, so it does not warrant needs-info.

Agent brief: Implement an external PR review queue in `factory dashboard`. In factory/dashboard.py add a snapshot that classifies each opted-in PR into one of six states (pending review, changes requested, CI pending, CI failed, ready, escalated) using repository-local PR number, title, author, current head SHA, last reviewed SHA, review verdict, and CI state. Render the queue and inbox in factory/dashboard.html, linking each row to GitHub and surfacing actionable stalled items in the existing inbox without adding any mutation controls. Add a snapshot test in tests/test_factory.py covering all six states and verifying PR links and SHA-specific review data, and document the review lane in README.md. Verification: `python -m unittest` passes and running `factory dashboard` displays the queue without changing labels, reviews, branches, or merge state. (Depends on #8.)

Note on inputs: `./review.diff` and `./snapshot` were absent; the cwd is the gated checkout (HEAD `e20ebcd`, merge-base with `origin/main` = issue base `8fba9c5`). Reviewed `git diff 8fba9c5..HEAD` (9 files, +1476/−37). Issue #79 fetched via GitHub API; `issue://` failed (no `GH_TOKEN`).

## Standards

Repo documents no CONTRIBUTING/AGENTS; only `pyproject.toml` (`[tool.ruff] line-length = 100`, not run in CI — `.github/workflows/ci.yml` runs `python -m unittest discover -s tests` only) and README. No hard violations.

Judgement-call smells (non-blocking):
- **Long Function**: `factory/feedback.py:79` — `collect` is ~360 lines with six nested closures (`gap`, `success`, `request`, `head`, `item`) plus three inline paging loops. Readable but every bound/coverage rule lives in one scope.
- **Private cross-module use**: `factory/feedback.py:406` imports `lifecycle._lifecycle`. The issue says to reuse lifecycle readers, so this is the right source; a public alias would make the contract explicit.
- Line length: many new lines exceed 100 (e.g. `feedback.py` item construction, `briefing.py`, `dashboard.html`). Pre-existing code already exceeds it and nothing enforces it — not a rule breach.
- `feedback.py` uses single-quoted strings; every other module uses double quotes. Style only.

## Spec

Checked against the normative schema, canonicalization, bounds, integration, and exit-gate items.

Satisfied (evidence):
- Envelope/required keys, enums, explicit nulls: `feedback.py:94-102`; coverage exactly `pr/reviews/threads/checks`: `feedback.py:23,101`.
- `source_revision` over exactly the 14 listed fields: `feedback.py:27-29,244`; `observation_id` over native IDs + head + sorted `(evidence_id, source_revision)` + coverage `status/truncated/reason`, excluding `observed_at`/URLs/order: `feedback.py:438-440`.
- Bounds as fixed constants (`100/2/20000/32/30`): `feedback.py:18-22`; initial+final head reads, head-race → partial on all sources + both SHAs in reason + relevance unknown: `feedback.py:369-380,422-425`.
- Items sorted by `evidence_id`, `source_revision`: `feedback.py:436`. Evidence ID namespaced provider/host/repo-id/PR-id/kind/source-id with URL-quoted components: `feedback.py:212`. Missing identity → bounded gap, no invented identity: `feedback.py:190-192`.
- Checks: native check-runs + commit statuses with IDs/head/status/conclusion/link, one page each: `feedback.py:338-367`. Reviews/threads over GraphQL with native IDs, `updatedAt`, commit, resolved/outdated, original commit: `feedback.py:35-46,279-334`.
- Factory review requires accepted lifecycle `result` (returncode 0, parsed, head==actual_head, enter/exit bracket) — never `VERDICT` prose: `feedback.py:406-420`; real dispatch emits those fields (`factory/dispatch.py:489-491`). `author.kind=human` never assigned; User vs Bot → `unknown`/`bot`: `feedback.py:211`.
- No `delivered/ready/approved` bits emitted anywhere in `feedback.py`.
- Full snapshot only: `factory/dashboard.py:884-896`; `--runtime-json` does not touch `snapshot()` (`dashboard.py:1228-1235`). Non-PR tickets untouched (`n in raw_prs` guard, `dashboard.py:884`).
- Legacy fields preserved: `pr_record` untouched; `"pr": pr` passthrough `dashboard.py:795`; test `tests/test_factory.py` `test_full_snapshot_preserves_legacy_contract_and_attaches_feedback` asserts `gate_text/review_decision/checks/comments/verdicts`.
- Briefing: feedback appended after existing candidates, omissions/shortening reported, missing-key/unknown-version rendered as unsupported: `factory/briefing.py:289-335,369-379`.
- Review drawer/Inbox: body via `pre()` (text node), links via `safeSourceURL`, coverage/errors/owner rendered: `factory/dashboard.html:993-1084,1086,1483`.
- README/CHANGELOG document schema location, bounds, unknown/partial, read-only authority.

Deviations / unrequested behavior (none rise to Required):
1. **Detail reads only for OPEN PRs** — `dashboard.py:895`, `feedback.py:128-131`. Not in the issue text or comments; closed/merged PRs get the schema-1 envelope with all sources `unavailable`/`not_collected`. The spec permits "explicit unavailable coverage" when a source "cannot be read", and the bound is documented in README/CHANGELOG. Scope deviation, not a contract violation; #15 only acts on open PRs. Note it in the handoff.
2. **`ownership_unverified` downgrades `coverage.pr` to `partial`** — `feedback.py:402-403`. The `pr` source was read completely; the spec puts ownership conflicts in `owner.relation/evidence`, and coverage is defined as read completeness. Conflating them makes every non-Factory PR report partial `pr` coverage (and shifts `observation_id`). Conservative direction, so non-blocking; cleaner to leave it as an error without touching status.
3. **Response received after deadline is discarded** — `feedback.py:137-138`. A successfully returned page is thrown away as `timeout`. Defensible under "stop cleanly at each bound"; mention only because the spec also says facts actually observed should be retained.
4. Legacy UI relabeling (`tabReview` chips lose PASS/FAIL colour, "Legacy …" wording in `taskFacts`, briefing labels) — `dashboard.html:1086-1100,1260-1268`, `briefing.py:209,270`. Within "task facts / Review drawer" touches and consistent with "legacy verdict comments render as non-actionable". Existing tests still pass per gate.

Exit-gate coverage not evidenced by the diff or gate report: item 4's "`--runtime-json` performs no feedback/GitHub read" has no new test (structurally true, see above); item 4's `<img onerror=...>` literal rendering is only exercised producer-side (`tests/test_feedback.py:17`), not in the DOM; items 5 (disposable-repo CLI + real browser) and 7 (handoff packet) are not in the gate report — comment 2 contains a truncated sanitized schema-1 example only.

## Plan drift

Plan status: not recorded, so file-level drift against the plan cannot be computed. Against the issue's **Touches** list, every changed file is named (`feedback.py`, `dashboard.py`, `briefing.py`, `dashboard.html`, `test_factory.py`, `test_briefing.py`, `README.md`, `CHANGELOG.md`; `tests/test_feedback.py` is explicitly permitted). Gate report shows only conflict-markers/test/leak-scan; the issue's exit-gate smoke (item 5) and handoff (item 7) proofs are absent from it. No drift exposes a required defect.

## Required fixes

None.

## Optional suggestions

- `feedback.py:402-403`: record `ownership_unverified` as an error without flipping `coverage.pr.status` to `partial`.
- `feedback.py:137-138`: retain a page that arrived after the deadline, and mark the *subsequent* reads as timed out.
- `feedback.py:406`: export a public `lifecycle.is_lifecycle` (or similar) instead of calling `_lifecycle`.
- Handoff: state the open-PR-only detail policy explicitly for #15 alongside the schema constants.

VERDICT: APPROVE

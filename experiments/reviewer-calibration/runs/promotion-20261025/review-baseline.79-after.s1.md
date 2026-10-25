## Standards

No `AGENTS.md`/`CONTRIBUTING.md`/`docs/` coding rules exist in the snapshot; the only documented tooling is `pyproject.toml:36-38` (`[tool.ruff] line-length = 100`), which the gate does not run. Nothing in the diff violates a documented repo rule.

Judgement-call smells (non-blocking):
- **Inconsistent style** — `factory/feedback.py` uses single-quoted strings throughout (diff 640-646, 711-719) while every sibling module uses double quotes; several lines exceed the configured ruff width (diff 829, 971, 1009). No rule is enforced, so optional.
- **Test coupling** — `tests/test_factory.py` imports `Provider, REPO, PR, ISSUE, H, AT, factory_events` from `tests/test_feedback.py` (diff 1259, 1331). Issue allowed a separate `test_feedback.py` "only if smaller and clearer"; cross-module fixture imports cut against that. Judgement, not a rule.
- `lifecycle._lifecycle` (private) is called from `feedback.py` (diff 1023). Issue said to reuse the accepted lifecycle readers; there is no public validator, so this is the available seam.

Safety checks passed: `pre()` is a text-node helper (`dashboard.html:938`), `h()` creates text nodes for string children and skips `false` attrs (`dashboard.html:457,464`), so `open: expanded` and `item.body` (diff 359, 381) render safely; `safeSourceURL` gates all hrefs (diff 314-317). `--runtime-json` path (`dashboard.py:1228-1254`) never reaches `snapshot()`. No import cycle (`lifecycle.py` imports nothing from `factory`).

## Spec (issue #79 + agent brief)

Satisfied:
- Schema-1 envelope with all required keys, enums, explicit nulls (diff 711-719, 829-838); `source_revision` over exactly the 14 listed fields (644-646, 861); `observation_id` over native IDs, final head, sorted `(evidence_id, source_revision)`, coverage `status/truncated/reason` and excluding `observed_at` (1053-1057).
- Fixed bounds `100/2/20000/32/30` (635-639); initial+final head reads around the detail group (878, 986); head race → partial coverage with both SHAs, relevance `unknown` (993-997, 1042); final-head failure keeps facts and nulls head (1650-1659 test).
- Outdated threads never promoted by SHA equality (1044-1047); missing source SHA never filled (824-826); Factory review requires a valid lifecycle `result` with `returncode 0`, `parsed`, `head == actual_head`, bracketing `enter`/`exit` (1023-1037); provenance-free `VERDICT` rejected (1492-1497 test).
- One-source failure keeps independent facts, secrets sanitized (759-762, 1554-1561); closed/merged PRs get a schema-1 envelope with `not_collected` and zero reads (745-748, 1681-1706).
- Full-snapshot only, `repository.id` from the main query (`dashboard.py:816` already binds `repo`; diff 469, 601); runtime-JSON untouched; non-PR tickets get no feedback (1326).
- `sources_for` appends feedback after existing candidates, reports omissions/shortening, keeps schema-missing/unsupported/partial as explicit notices (156-215, 239-247, 255-256); existing human decisions retain priority (1222-1233 test).
- Review drawer, task facts and Inbox raw evidence render provenance links, source vs observed head, relevance, disposition, location, coverage and errors (299-391, 426-440, 448). Legacy `checks/comments/verdicts/review_decision/gate_text` preserved (1319-1323 test).
- README/CHANGELOG document schema location, bounds, unknown/partial semantics and read-only authority (9, 21-105).

Not verifiable from the diff (not blocking): exit-gate 5 (real CLI/browser smoke in a disposable repo) and exit-gate 7 (handoff packet) live outside the diff; the `<img onerror=...>` literal-render check is browser-only. The escalation comment shows a sanitized snapshot example was produced, so the handoff artifact exists.

## Plan drift

Plan status: not recorded — nothing to diff against. All changed files (`CHANGELOG.md`, `README.md`, `factory/{briefing.py,dashboard.html,dashboard.py,feedback.py}`, `tests/{test_briefing.py,test_factory.py,test_feedback.py}`) are named in the issue's Touches. Gate report covers only conflict/test/leak-scan; the issue's smoke proof (exit gate 5) is not evidenced there and cannot be judged from the diff. No drift exposes a defect.

## Required fixes

None.

## Optional suggestions

1. `factory/feedback.py` diff 1019-1020: `ownership_unverified` is recorded as a `pr` coverage gap, so `coverage.pr.status` becomes `partial` (and `observation_id` shifts) for every PR without verified Factory ownership even when the PR read itself was complete. The drawer then shows `pr partial` (diff 343). Ownership already has its own `relation`; consider keeping it out of `pr` coverage so `partial` keeps meaning "read incomplete".
2. `factory/dashboard.py` diff 596-608: each selected open PR now costs up to 8 `gh` calls under a 30 s group timeout inside the TTL-15 s cached `snapshot()` (`dashboard.py:84,968`). With several open agent PRs, `/api/snapshot` and `--json` latency will exceed the TTL. Spec places collection here, so no change required; worth noting in the handoff.
3. `feedback.py` diff 868: removing a conflicting item does not decrement `counts[source]`; harmless off-by-one against `ITEM_LIMIT`.
4. Style: align `feedback.py` quoting/line width with sibling modules if a formatter is ever added to the gate.

VERDICT: APPROVE

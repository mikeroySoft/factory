## Standards

Sources checked: README.md (feature section + limits/consumer rules), `docs/autonomy-execution-plan.md:384-387` (B2 file set — diff matches exactly), existing `dashboard.py`/`briefing.py` conventions, `pyproject.toml` (ruff line-length only; style excluded as gate-established). No AGENTS.md/CONTRIBUTING.md in repo.

### Required
None.

### Optional
- `tests/test_briefing.py:136-138,167-170` — assertions match English notice prose from `factory/briefing.py:295-301` (`"no schema-versioned feedback object"`, `"unsupported/unknown"`). README.md:1266-1267 ("tolerate structured codes, not match English wording") governs C1 evidence consumers, not tests, so not a rule breach; but structural checks (e.g. `assertFalse(any(label.startswith("PR feedback ·")))`, already used at `tests/test_briefing.py:231`) would survive rewording.
- `factory/feedback.py:81` — `observed_at` parameter has no production caller (`factory/dashboard.py:888-896` omits it); only tests use it as an injectable clock. Speculative Generality judgement call; harmless.
- `factory/dashboard.html:1035,1278` — `['pr','reviews','threads','checks']` duplicated in `feedbackEvidence()` and `taskFacts()`; `factory/dashboard.html:1008-1011` re-creates `sourceLink` per render instead of reusing the existing `safeSourceURL` anchor pattern at `:1244-1248`. Duplicated Code judgement calls.
- `factory/dashboard.html:1096-1099` — legacy verdict chips drop `PASS/FAIL` colouring (`class: 'chk'`). Consistent with the issue's "legacy verdict … non-actionable" rendering requirement; noting only as a visible change to an existing surface.

Verified clean: body/location rendered only via `pre()`/`h()` text nodes (`factory/dashboard.html:453-465,1074`), no `innerHTML`; `h()` skips `open: false` (`:457`); `lifecycle._lifecycle` cross-module use has precedent (`factory/gate.py:184`, `factory/runtime_events.py:171`).

## Spec (issue #79)

### Required
None.

### Optional
- **Scope narrowing not in the brief — surface for human acceptance.** `factory/dashboard.py:898-907` passes `collect_details=raw.get("state") == "OPEN"`; `factory/feedback.py:129-131,367` then records every source as `unavailable`/`not_collected` with `items: []` for merged/closed PRs, and `head_sha` stays null (`factory/feedback.py:96`) even though `headRefOid` is already known from the list query (`factory/dashboard.py:147`). The issue never authorizes skipping readable sources ("When a PR is present but a feedback source *cannot be read*, return … explicit unavailable/partial coverage"), but it also requires "Preserve full-snapshot cadence" and fixed bounds, which 100 PRs × up to 5 reads × 30 s would violate. Coverage/errors are explicit, README.md:717-724 and CHANGELOG.md:9 document it, and #15's consumer targets open `agent/*` PRs — so not a correctness defect, but it is an unapproved scope decision. Not blocking.
- `factory/dashboard.html:1090-1095,1259-1267`, `factory/briefing.py:209,272` — rewording unrelated legacy surfaces ("Legacy local gate failures", "factory-approved label present", relabelled `review-{n}.md`). Issue Touches says `dashboard.html` changes are "coverage presentation only". Editorial drift, no defect.
- Exit gate 4 `--runtime-json` boundary: no new test in the diff, but the pre-existing audit-hook harness `tests/test_runtime.py:18-51` already fails on any `gh` spawn/network call in that path, and `factory/dashboard.py:1228-1254` never reaches `snapshot()`/`feedback`. Covered.
- Exit gate 4 `<img onerror=…>` literal render: only the collector pass-through is tested (`tests/test_feedback.py:23`); no JS harness exists in the repo, deferred to the manual gate-5 browser check. Pre-existing limitation.

Verified against spec: schema keys/enums (`factory/feedback.py:94-102`), `REVISION_FIELDS` exactly the 14 listed (`:33-35,244`), `observation_id` inputs/exclusions (`:438-440`), item sort (`:436`), constants (`:19-23`), initial/final head reads + race → `unknown` with both heads in reason (`:261,369-380,425`), `thread_outdated=true` → historical (`:429-430`), missing source SHA never filled (`:207-209,217`), evidence_id namespacing (`:212`) and no identity without source ID (`:189-192`), no `delivered/approved/ready` fields, `factory_review` requires accepted lifecycle result with matching head (`:406-420`), VERDICT prose rejected (`tests/test_feedback.py:98-100`), sanitized fixed-string errors (`:143,145`), one-source failure isolation (`request()` per source), legacy `pr.checks/comments/verdicts/review_decision/gate_text` untouched (`pr_record` not in diff; `tests/test_factory.py:2469-2474`), briefing appends feedback after human constraints and reports omission (`factory/briefing.py:321-330,372-380`; `tests/test_briefing.py:222-233`).

## Changed signatures/defaults — consumers outside the diff

- `dashboard.github(*, endpoint, query, variables, timeout)` (`factory/dashboard.py:196-222`): only caller outside feedback is `snapshot()` at `factory/dashboard.py:815` with no args → identical command/variables as before (`-f` for strings vs old `-F` is safe for slugs). `errors`→raise is not a regression: `gh api` already exits non-zero on GraphQL errors. **Holds.**
- `GRAPHQL` adds `repository.id`, `issues.id`, `pullRequests.id/headRefOid` (`factory/dashboard.py:124,128,155`): `pr_record` (`:243`) ignores extra keys; `factory/evidence.py:936-939` builds its own REST-shaped dict for `pr_record` without them — `pr_record` doesn't require them. **Holds.**
- `briefing.sources_for` signature unchanged; "Evidence coverage" source label/`truncated` now conditional (`factory/briefing.py:405-407`, was always `· truncated`/True): `factory/evidence.py:584` filters by `startswith("Evidence coverage")` → still matches; `factory/evidence.py:1057-1059` forwards `truncated` as-is; `factory/briefing.py:533` unchanged. **Holds.**
- New `tickets[].pr.feedback` key: `factory/evidence.py:989` builds tickets via `build_ticket` without it → renders as "unsupported/unknown" by design (bounded evidence CLI is not the full snapshot). **Holds.**
- `--runtime-json` (`factory/dashboard.py:1228-1254`): untouched, no `snapshot()`/`feedback` reach. **Holds.**

**Summary:** Standards — 0 required, 4 optional (worst: prose-matching test assertions). Spec — 0 required, 4 optional (worst: open-PR-only detail collection is an undocumented-in-brief scope decision; documented and explicit in coverage, flagged for human acceptance).

VERDICT: APPROVE

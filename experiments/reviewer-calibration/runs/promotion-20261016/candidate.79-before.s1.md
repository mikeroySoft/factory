## Standards

No `AGENTS.md`/`CONTRIBUTING.md` in the repo; `docs/*.md` are planning documents, not coding rules. Only the smell baseline applies (judgement calls).

- **Long Function (judgement)** — `factory/feedback.py:652-989` (`collect`, ~330 lines with six closures sharing mutable state `result/reasons/counts/identities/conflicts`). It traces linearly and the spec forces the bound/race/coverage coupling, so not blocking.
- **Private cross-module call** — `factory/feedback.py:954` uses `lifecycle._lifecycle`. Precedent exists (`factory/runtime_events.py:171`), so consistent with repo practice.
- Quote style: `feedback.py` is single-quoted throughout; every other module in `factory/` is double-quoted. Style only, not a documented rule.

## Spec

### Required fixes

1. **`producer.revision` can report a foreign repository's HEAD** — `factory/dashboard.py:555-558` (diff lines 555-558):
   ```python
   source_root = Path(__file__).resolve().parent
   revision = sh(["git", "rev-parse", "HEAD"], cwd=source_root).strip()
   if sh(["git", "status", "--porcelain", "--", "."], cwd=source_root).strip():
       revision = None
   ```
   Trigger: factory installed into a `.venv`/site-packages that sits inside the *consumer's* git checkout (the normal `uv`/`venv` project layout; `.venv` is gitignored). `git rev-parse HEAD` resolves the consumer repo's HEAD; `git status --porcelain -- .` prints nothing for an ignored tree. Result: `producer.revision` = the consumer project's commit, which passes `sha()` and is emitted as Factory's "verifiable source/build revision". Rule: issue #79 schema-1 `producer`: "`revision` is a verifiable source/build revision when available, otherwise null". Impact: #15's handoff provenance (`README.md:85-86` says "clean source-checkout Git revision, otherwise null") would carry a false revision. Fix is one more guard: require `sh(["git","ls-files","--error-unmatch","feedback.py"], cwd=source_root)` to be non-empty before trusting `rev-parse`.

### Optional suggestions

- `factory/dashboard.py:499` — `raise RuntimeError("GitHub read failed")` replaces the previous `proc.stderr` text for the *existing* default-query path too, so `snapshot()["errors"]` (dashboard.py:829) loses the `gh` auth hint operators relied on. Sanitization is required for feedback errors, but the main query could keep stderr (or log it) without affecting the feedback contract. `tests/test_lifecycle_consumers.py:140` mocks `github`, so unaffected.
- `factory/briefing.py:186` — per-item `truncated=bool(item.get("truncated"))` makes `dashboard.html:1244` render "shortened to fit the evidence limit" for bodies truncated by the *collector*, not the briefing budget. The payload's own `truncated` field already carries that fact; dropping the briefing flag would avoid the misleading sentence.
- `factory/feedback.py:842-845` — a reviews page 2 returning exactly 50 rows is flagged `page_limit`/`truncated` even when no page 3 exists. Conservative and spec-tolerated ("absence beyond a cap is never complete evidence"), but produces a false `partial` on exactly-100-review PRs.
- `factory/feedback.py:817-820` — on a same-ID conflict the earlier item is removed but `counts[source]` is not decremented; harmless (cap becomes slightly stricter).

### Acceptance-criteria trace

- AC1 simultaneous CI/review/thread/factory-review with IDs/links/heads/location/disposition/ownership/nulls/complete coverage: `tests/test_feedback.py:1272-1309`; provenance-free `VERDICT` rejected: `test_feedback.py:1304-1309`, `tests/test_factory.py:1203-1206`.
- AC2 reorder + timestamp idempotence, semantic revision changes, new check-run identity: `test_feedback.py:1311-1342`.
- AC3 same/advanced/raced head, missing SHA, dismissed, resolved/outdated, check states, empty-complete, caps, body truncation, timeout, malformed, one-source failure: `test_feedback.py:1344-1485`.
- AC4 legacy fields preserved: `test_factory.py:1200-1204`; no-feedback-key → unsupported: `tests/test_briefing.py:1117,1125`, `dashboard.html:278-282,396-397`; `--runtime-json` path untouched (`dashboard.py:1224-1250` never reaches `snapshot()`; existing `tests/test_runtime.py:21-50` subprocess audit still guards it); non-PR ticket: `test_factory.py:1207`; `<img onerror>` body goes through `pre()` text nodes (`dashboard.html:938`, used at diff 350); human constraints ahead of feedback + omission notice: `test_briefing.py:1086-1091,1132-1143`.
- AC5/7 (disposable smoke, browser, handoff) are process evidence outside the diff; not assessed here.
- Not emitted: no `delivered`/`fixed`/`approved_for_merge`/`ready` keys anywhere in `feedback.py`.

### Changed signatures/defaults — outside-diff consumers

- `dashboard.github(*, endpoint, query, variables, timeout)` — only caller `factory/dashboard.py:815` (`github()` bare → default-query branch, still holds). `-F`→`-f` for string variables is equivalent for `owner`/`name`.
- `GRAPHQL` adds `repository.id`, `issues.nodes.id`, `pullRequests.nodes.{id,headRefOid}` — consumers `snapshot` (dashboard.py:816-822) and `pr_record` (dashboard.py:243) read by key; additive, hold. `repo.get("id")` at diff 566 is populated by dashboard.py:816.
- `briefing.sources_for` signature unchanged; "Evidence coverage" label now conditionally suffixed — only consumer `sourceDetails` (`dashboard.html:1242`) renders the label verbatim; holds.
- `briefing.bounded_file(root, rel, cap, tail=True)` reused from `dashboard.py` — signature at `briefing.py:148` matches.
- Lifecycle `result` event fields consumed at `feedback.py:960-965` (`returncode`, `parsed`, `head`, `actual_head`, `verdict`; exit `outcome` ∈ `approved|product_feedback`) match the producer at `factory/dispatch.py:476-492`; `claimed` event at `dispatch.py:1297` carries `ticket`.
- `tabReview`/`taskFacts`/`renderTaskBriefing` — internal to `dashboard.html`; `h()` (`dashboard.html:457`) drops `open: false`/`null` children, so the new `details`/conditional kids render correctly.

Summary: Standards — 0 hard violations, 2 judgement-call smells. Spec — 1 required (false `producer.revision` in venv-inside-repo installs), 4 optional.

VERDICT: REVISE

## Review: `origin/main..HEAD` vs issue #79 (B2)

Gate report (conflict-markers/test/leak-scan PASS) taken as authoritative for build/test. Diff-only review below.

### Changed signatures / defaults / labels — consumer check

| Change | Consumers outside diff | Holds? |
|---|---|---|
| `dashboard.github()` now keyword-only `(endpoint, query, variables, timeout)` (`factory/dashboard.py:196-221`) | Only `factory/dashboard.py:815` (bare `github()`); tests mock it by name (`tests/test_factory.py:1186`) | Yes |
| GraphQL vars now passed with `-f` (strings) / `-F` (ints) instead of all `-F` (`factory/dashboard.py:210-212`) | `gh api` only | Yes (safer: repo names like `1234` no longer coerced) |
| `GRAPHQL` adds `repository.id`, `issues.id`, `pullRequests.id/headRefOid` (`factory/dashboard.py:124,127,150`) | `pr_record` / `build_ticket` ignore extra keys; signatures unchanged (`factory/dashboard.py:243,725`) | Yes |
| `sources_for` signature unchanged; label `review-{n}.md` renamed `"Legacy review verdict · provenance unknown"` (`factory/briefing.py:208`) | `factory/dashboard.html:1089` resolves it via `/verdict\|review/i` on label (`:1542`) — still matches, and feedback sources are appended after so ordering is preserved | Yes |
| Coverage source label/`truncated` now conditional instead of always `· truncated`/`True` (`factory/briefing.py:386-398`) | `factory/dashboard.html:1242` reads `truncated` generically; `factory/evidence.py:1059` passes it through | Yes |
| `sources_for` now emits a feedback notice when `pr` has no `feedback` key (`factory/briefing.py:291-296`) | `factory/evidence.py:1040-1057` builds its own ticket without `feedback`, so `factory evidence` output's coverage source now carries "PR feedback is unsupported/unknown…" | Holds (correct per spec "no `feedback` key is unsupported/unknown"); observable change for that CLI's consumers — see Optional 3 |
| `--runtime-json` path (`factory/dashboard.py:1224-1250`) | Untouched; existing `tests/test_runtime.py:18-51` audit hook forbids any `gh` subprocess there and passed in the gate | Yes |

### Required fixes

None.

Spec checked clause-by-clause against `factory/feedback.py`: envelope keys and full `items[]` field set with explicit nulls (`factory/feedback.py:779-788`); `evidence_id` namespacing (`:779`); `REVISION_FIELDS` exactly the spec list, excluding `source_url`/relevance/observed head (`:24-26`, `:811`); `observation_id` over native IDs + final head + sorted `(evidence_id, source_revision)` + coverage status/truncated/reason, excluding every timestamp (`:986-988`); constants 100/2/20000/32/30 (`:18-22`); initial+final head reads with both heads in the bounded reason and relevance forced `unknown` (`:750`, `:920-931`, `:973`); `thread_outdated=true` → `historical` regardless of SHA equality (`:975-978`); missing source SHA never filled from head (`:774-776`); one-source failure isolated per `request()` (`:695-710`); `factory_review` only from a valid lifecycle `review/result` row with `returncode==0`, `parsed`, `head==actual_head`, bracketing `enter`/`exit` (`:954-968`) — matches what `dispatch.py:489-492` actually emits; ownership requires same-repo closing link (id+url equality) **and** a retained `claimed` row (`:937-945`, `dispatch.py:1297`); no `delivered`/`approved`/`ready` keys anywhere. Dashboard HTML renders body/evidence only via existing `pre()`/`h()` text nodes (`factory/dashboard.html:938`, `:464`) and `safeSourceURL` (`:1225`); `open: false` is dropped by `h()` (`:457`) so collapsed Inbox items work. Briefing appends feedback after all pre-existing candidates and counts omitted/shortened feedback separately (`factory/briefing.py:289`, `:343-351`, `:373-381`). Out-of-scope files untouched.

### Optional suggestions

1. **Snapshot latency / rate budget** — `factory/dashboard.py:883-892` runs `feedback.collect` per PR-bearing ticket inside `snapshot()`, i.e. up to 8 sequential `gh` subprocesses and a 30 s detail deadline **per PR**, under `_cache_lock` (`factory/dashboard.py:962-967`) on every `SNAPSHOT_TTL` miss. Spec mandates in-snapshot collection with no cache, so not blocking, but worst-case `/api/snapshot` time is `N_PRs × 30 s` and all request threads block on the lock. Worth a README caveat or a snapshot-level ceiling later if it bites.
2. **Transport error text lost** — `factory/dashboard.py:216` replaces `proc.stderr.strip()` with the constant `"GitHub read failed"`, so the pre-existing snapshot `errors[]` entry (`:829`) no longer shows `gh` auth/404 diagnostics. `feedback.request()` already maps every exception to fixed messages (`factory/feedback.py:706-709`), so sanitising at the transport was unnecessary; consider restoring stderr for the bare full-snapshot call only.
3. **`factory evidence` bundle now reports feedback as unsupported** (`factory/evidence.py:1057` → `factory/briefing.py:291-296`). Correct per spec, but if that bundle is meant to be a complete read surface it should eventually attach the same object; otherwise document the limitation.
4. **Coverage `pr: partial` when ownership is merely unverified** — `factory/feedback.py:950-951` records `ownership_unverified` as a gap after a fully successful PR read, so `coverage.pr.status` becomes `partial` for any non-Factory PR. Spec defines coverage as read completeness; ownership state is already explicit in `owner.relation`. Judgement call; conservative direction.
5. **`relation` never becomes `"none"` when an issue is supplied but no closing link exists** (`factory/feedback.py:946`): stays `"unverified"`. Spec wording is ambiguous; harmless.
6. **Check-run attribution** — `author=row.get('creator')` (`factory/feedback.py:909`): REST check-run objects carry `app`, not `creator`, so check-run authors are always `{login: null, kind: "unknown"}`. Allowed by schema; `app.slug` would be evidence-backed attribution if wanted. Likewise REST reviews expose `submitted_at`, not `updated_at` (`:839`), so review `source_updated_at` is always null and any truncated review body gets `change_detection_incomplete`.
7. **Reviews page cap flagged on exact multiples** — `factory/feedback.py:842-845` marks `page_limit`/truncated when page 2 returns exactly 50 rows even if no page 3 exists. Conservative; fine.
8. Smell (judgement): `collect()` is ~300 lines with five closures and ~15 string-tagged `gap(source, …)` sites (`factory/feedback.py:680-825`); consistent with the repo's dict-oriented style, no action needed now. `lifecycle._lifecycle` private import (`:954`) follows existing repo practice (`dashboard.py`, `gate.py`).
9. Exit-gate items 5 (disposable-repo smoke + browser) and 7 (handoff packet with sanitized example) are not diff-verifiable; README (`README.md:699-772`) documents schema/bounds/unknown-partial/read-only in prose without a literal JSON example — the spec places the example in PR/handoff evidence, so not a doc defect here.

VERDICT: APPROVE

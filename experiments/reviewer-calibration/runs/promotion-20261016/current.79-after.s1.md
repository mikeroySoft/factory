# Review: `origin/main..HEAD` (8fba9c5 → e20ebcd) vs. issue #79

Base is the issue's stated canonical `main` (`8fba9c5`). No `AGENTS.md`/`CONTRIBUTING.md` exist at that base; standards sources are `README.md`, `docs/`, and `pyproject.toml`. Gate (conflict-markers/test/leak-scan) PASS is taken as given.

## Standards

No hard violations found.

- Only convention-level tooling is `[tool.ruff] line-length = 100` (`pyproject.toml`), and it is not a gate check; `factory/briefing.py` already had 55 lines >100 at base, so this is not an enforced repo rule. Not reported.
- Reuse checks out: `factory/dashboard.py:853` reuses `briefing.bounded_file`/`EVENT_READ_CAP` for provenance instead of a second ledger reader; `factory/feedback.py:406` reuses `lifecycle._lifecycle` (precedent: `factory/runtime_events.py:171`); the new transport is one widened `github()` (`factory/dashboard.py:510-537`), not a second client. Matches the ticket's "no second GitHub client / no provider framework" rule.
- Smell baseline (judgement calls only): `collect()` in `factory/feedback.py:79-441` is one ~360-line function with nested closures (`gap`/`success`/`request`/`head`/`item`). Long Function, but it keeps all state local and the ticket explicitly asked for a single narrow module; not flagging.

## Spec

**Acceptance criteria met (evidence):**

- Schema-1 envelope with required keys, enums, explicit nulls: `factory/feedback.py:94-102`, `:212-221`.
- `source_revision` over exactly the specified fields, excluding `source_url`/relevance/observed head: `factory/feedback.py:27-29`, `:244`. `observation_id` over native IDs + final head + sorted `(evidence_id, source_revision)` + coverage `status/truncated/reason`, excluding `observed_at`: `:438-440`.
- Fixed bounds (100/2 pages/20 000 B/32 errors/30 s) as module constants, no runtime policy: `factory/feedback.py:18-22`; stop-and-mark at each bound: `:193-205`, `:284-286`, `:322-327`, `:360-361`.
- Head race → `relevance=unknown`, both heads in reason, final head used for `pr.head_sha`: `factory/feedback.py:369-380`, `:422-426`. Missing source SHA never filled from head: `:207-209`. `thread_outdated=true` → historical regardless of SHA equality: `:427-433`.
- Factory review requires accepted lifecycle `result` with `returncode==0`, `parsed`, `head==actual_head`, enclosing `enter`/`exit` with matching outcome; `VERDICT` prose never promoted: `factory/feedback.py:405-420`; dashboard verdict comments relabelled legacy: `factory/dashboard.html:1395-1401`, `factory/briefing.py:147-148`.
- Ownership needs same-repo closing link + retained `claimed` event; branch text/author never used: `factory/feedback.py:384-399`.
- Sanitized errors: messages are fixed strings; provider stderr is swallowed in `request()` (`factory/feedback.py:142-146`); test asserts no secret leaks (`tests/test_feedback.py:1561`).
- Full snapshot only: `factory/dashboard.py:883-896`; `--runtime-json` branch (`factory/dashboard.py:1228-1255`) untouched and still covered by the existing audit-hook test forbidding external commands (`tests/test_runtime.py:48`).
- Legacy `checks/comments/verdicts/review_decision/gate_text` preserved: `tests/test_factory.py:1319-1323`. Non-PR tickets gain no feedback: `tests/test_factory.py:1326`.
- Briefing: feedback appended after existing sources (`factory/briefing.py:157`, `:206-215`), feedback omissions/shortening reported explicitly (`:239-247`), human decisions retained under cap (`tests/test_briefing.py:1222-1233`), missing/unknown schema → "unsupported/unknown" not empty (`factory/briefing.py:160-177`). No model call (`tests/test_briefing.py:1150`).
- Body rendered through `pre()`→`h()`→`createTextNode` only (`factory/dashboard.html:381`, helper at `:453-466`); links through `safeSourceURL` (`:314-317`).
- README/CHANGELOG document schema location, bounds, unknown/partial semantics, read-only authority: `README.md:699-783`, `CHANGELOG.md:9`.

## Required fixes (blocking)

None.

## Optional suggestions (non-blocking)

1. **Undocumented-in-issue scope narrowing: detail reads only for `OPEN` PRs.** `factory/dashboard.py:895` (`collect_details=raw.get("state") == "OPEN"`), `README.md:717-724`, `CHANGELOG.md:9`. The issue text says to query reviews/threads/checks for the ticket's PR and lists no open-only bound; the issue comments contain no approved scope change for this. Rationale (snapshot cadence, up to 100 PRs × ≥6 provider calls) is sound and the envelope stays schema-1 with explicit `not_collected`, so this is not an acceptance-criterion failure—but the owner should explicitly acknowledge it at handoff since #15's producer contract is defined by this behaviour.
2. **`coverage.pr` becomes `partial` when ownership is merely unverified.** `factory/feedback.py:402-403`. Coverage per the spec describes read completeness; a fully read PR whose body lacks `Closes #n` will render `pr partial` in the drawer (`factory/dashboard.html:343` maps it to `PENDING`) and the briefing's "partial … is unknown, not empty" notice. The fact is already in `owner.relation` and `errors[]`; dropping the coverage downgrade would keep coverage semantics clean. Also feeds `observation_id`, so changing it later changes identities.
3. **Final head read is inside the 30 s detail deadline.** `factory/feedback.py:127-146`, `:369`. Spec places the head reads "around the detail group" with the timeout applying to the group. If details exhaust the budget, the final head read is refused and every item (including successfully read checks) degrades to `relevance=unknown`. Conservative, never wrong, but a small separate allowance for the final head read would preserve classification after a slow source.
4. **Cross-test import.** `tests/test_factory.py:2410`, `:2482` import `tests.test_feedback`; no precedent at base and `tests/` has no `__init__.py`, so it relies on cwd being repo root (true for the documented gate command). Consider moving `Provider`/fixtures or keeping it as is with that caveat.
5. **Gate wording relabelled "Legacy".** `factory/dashboard.html:1260` now says "Legacy local gate failures/context". The deterministic gate is the evidence of record (`README.md:87`); it is not PR feedback and not legacy. Suggest reverting that one string; the verdict/CI-rollup relabels are spec-driven and fine.

VERDICT: APPROVE
